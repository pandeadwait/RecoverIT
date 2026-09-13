"""Async boundary adapters for the merged Person 1, 2, and 3 implementations."""

from __future__ import annotations

import asyncio

from collectors.gateway.collection_service import DefaultCollectionService
from contracts.collection import RawEvidenceBatch as Person2RawEvidenceBatch
from contracts.collection.capabilities import SourceCapabilityCatalog
from contracts.collection.query_plan import EvidenceQueryPlan as Person1EvidenceQueryPlan
from contracts.collection.schemas import RawEvidenceBatch as Person3RawEvidenceBatch
from contracts.context import IncidentContextSnapshot as Person2ContextSnapshot
from contracts.evidence import EvidenceFilter
from contracts.evidence.schemas import IncidentContextSnapshot as Person3ContextSnapshot
from contracts.incident import IncidentSeed as Person2IncidentSeed
from contracts.incident.schemas import IncidentSeed as Person3IncidentSeed
from contracts.investigation.schemas import EvidenceQueryPlan as Person3EvidenceQueryPlan
from evidence.application.service import EvidenceProcessingService
from evidence.context.service import ContextPublicationService
from evidence.repositories.ports import EvidenceOrder, EvidencePageRequest
from evidence.repositories.unit_of_work import InMemoryRepositoryCoordinator
from timeline.builder.service import TimelinePersistenceService


class Person1CollectionAdapter:
    """Expose Person 1's synchronous collector through Person 3's async protocol."""

    def __init__(
        self,
        service: DefaultCollectionService,
        catalog: SourceCapabilityCatalog | None = None,
    ) -> None:
        self._service = service
        self._catalog = catalog

    async def execute(
        self, plan: Person3EvidenceQueryPlan
    ) -> Person3RawEvidenceBatch:
        plan_payload = plan.model_dump(mode="json")
        for query in plan_payload["queries"]:
            query.pop("schema_version", None)
        person1_plan = Person1EvidenceQueryPlan.model_validate(plan_payload)
        batch = await asyncio.to_thread(
            self._service.execute, person1_plan, self._catalog
        )
        return Person3RawEvidenceBatch.model_validate(batch.model_dump(mode="json"))


class Person2ContextAdapter:
    """Run Person 2's processing, timeline, and publication pipeline asynchronously."""

    def __init__(
        self,
        incident: Person3IncidentSeed | Person2IncidentSeed,
        coordinator: InMemoryRepositoryCoordinator | None = None,
    ) -> None:
        if isinstance(incident, Person3IncidentSeed):
            self._incident = Person2IncidentSeed.from_dict(
                incident.model_dump(mode="json")
            )
        else:
            self._incident = incident
        self._coordinator = coordinator or InMemoryRepositoryCoordinator()
        self._processing = EvidenceProcessingService(self._coordinator)
        self._timeline = TimelinePersistenceService(self._coordinator.timelines)
        self._publication = ContextPublicationService(self._coordinator)

    @property
    def coordinator(self) -> InMemoryRepositoryCoordinator:
        """Expose repositories for diagnostics and application composition."""

        return self._coordinator

    async def build(
        self,
        incident_id: str,
        batch: Person3RawEvidenceBatch,
        previous_context: Person3ContextSnapshot | None = None,
    ) -> Person3ContextSnapshot:
        return await asyncio.to_thread(
            self._build_sync, incident_id, batch, previous_context
        )

    def _build_sync(
        self,
        incident_id: str,
        batch: Person3RawEvidenceBatch,
        previous_context: Person3ContextSnapshot | None,
    ) -> Person3ContextSnapshot:
        if incident_id != self._incident.incident_id:
            raise ValueError("context request belongs to another incident")
        if previous_context is not None and previous_context.incident_id != incident_id:
            raise ValueError("previous context belongs to another incident")

        batch_payload = batch.model_dump(mode="json")
        for result in batch_payload["results"]:
            result.pop("schema_version", None)
            for record in result["records"]:
                record.pop("schema_version", None)
        person2_batch = Person2RawEvidenceBatch.from_dict(batch_payload)
        processing = self._processing.process(self._incident, (person2_batch,))
        evidence_filter = EvidenceFilter(
            schema_version="1.0",
            incident_id=incident_id,
            include_unknown_event_time=True,
            limit=2_147_483_647,
        )
        records = []
        cursor = None
        while True:
            page = self._coordinator.evidence.query_page(
                evidence_filter,
                EvidencePageRequest(
                    limit=1000,
                    cursor=cursor,
                    order=EvidenceOrder.EVENT_TIME_ASC,
                ),
            )
            records.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        persisted_previous: Person2ContextSnapshot | None = (
            self._coordinator.contexts.get_latest(incident_id)
        )
        revision = 1 if persisted_previous is None else persisted_previous.revision + 1
        self._timeline.build_and_persist(incident_id, tuple(records), revision)
        snapshot = self._publication.publish(
            self._incident,
            (person2_batch,),
            processing.warnings,
            previous_snapshot=persisted_previous,
        )
        return Person3ContextSnapshot.model_validate(snapshot.to_dict())
