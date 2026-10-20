# Phase 3 Classification and Canonical Normalization

## Boundary

`EvidenceNormalizationService` accepts only the shared `IncidentSeed` and
`RawEvidenceBatch` contracts. It produces immutable
`NormalizedEvidenceCandidate` values and does not persist, hash, redact,
deduplicate, build timelines, or reason about causes. Those responsibilities
remain in later Person 2 phases.

Candidates are pre-security, in-memory values. Their raw payload is retained
only for Phase 4 hashing and redaction, is excluded from object representations,
and is never included by `to_dict`.

## Classification

`ClassifierRegistry` routes by the six v1.0 source types. Each classifier emits
the same source-neutral shape: evidence type, deterministic summary, canonical
attributes, and optional service/resource identity inputs.

| Source | Canonical evidence type |
|---|---|
| logs | `error_event` or `log_event` |
| metrics | `metric_observation` |
| changes | `source_change` |
| deployments | `deployment_event` |
| pipelines | `pipeline_event` |
| configuration | `configuration_change` |

Summaries are whitespace-normalized, deterministically truncated, and never
created by an LLM. Classifiers copy only documented structured fields; no
collector or vendor payload class crosses the boundary.

## Time semantics

The timestamp normalizer keeps `event_time`, `observed_at`, and `collected_at`
separate and converts explicit timestamps to UTC. It records the original
timestamp text and UTC offset when that information remains available on the
input object. Missing event time stays `None`. Approximation and uncertainty are
accepted only from explicit `event_time_approximate` and `time_uncertainty_ms`
metadata; exact time is never inferred from collection or array order.

## Identity behavior

`IdentityResolver` is a port. `ConfigurationIdentityResolver` is the reference
adapter and accepts immutable service/resource alias mappings. Matching is
trimmed and case-insensitive. A configured alias yields its canonical ID. An
unknown alias is preserved rather than guessed and adds an `unknown_identity`
warning. If a record has no service, the incident's explicit service scope is
used as the identity input.

## Failure isolation and determinism

An incident mismatch rejects the batch with `BatchNormalizationError` carrying
a portable `ProcessingError`. A malformed individual record creates a
`malformed_record` warning with identifiers but no raw payload, while valid
siblings continue. Source warnings, status, and truncation are preserved.

Results and records are explicitly ordered by stable source/query/record keys,
so equivalent batches produce identical candidate projections independently of
input array order.
