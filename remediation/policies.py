"""Deterministic safety policies and validation for remediation plans.

Enforces non-negotiable safety rules:
1. Rejects executable shell syntax (e.g. kubectl, rm, git reset, docker, systemctl).
2. Verifies evidence grounding (all cited evidence must exist in hypothesis or context).
3. Downgrades contradictory, low-confidence, or inconclusive rankings to BLOCKED.
4. Scrubs credentials and secrets.
5. Emits safe BLOCKED fallback plans on any violation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import uuid

from contracts.enums import ConfidenceLabel, InvestigationStatus
from contracts.evidence.schemas import IncidentContextSnapshot
from contracts.hypothesis.schemas import RankedHypothesisSet
from contracts.remediation.schemas import RemediationPlan, RemediationRisk

# Disallowed executable command patterns
COMMAND_SYNTAX_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)\bkubectl\s+[a-z]+"),
    re.compile(r"(?i)\bdocker\s+[a-z]+"),
    re.compile(r"(?i)\bsystemctl\s+[a-z]+"),
    re.compile(r"(?i)\bhelm\s+[a-z]+"),
    re.compile(r"(?i)\bgit\s+(reset|checkout|revert|push|commit|rebase)\b"),
    re.compile(r"(?i)\brm\s+-[a-zA-Z]*f"),
    re.compile(r"(?i)\bcurl\b.*\|\s*(ba)?sh"),
    re.compile(r"(?i)\b(kill\s+-\d+|killall|pkill)\b"),
    re.compile(r"(?i)\bsudo\s+[a-z]+"),
    re.compile(r"(?i)\bchmod\s+[0-7]+"),
    re.compile(r"(?i)\b(DROP\s+TABLE|DELETE\s+FROM|TRUNCATE\s+TABLE)\b"),
]

# Sensitive credentials / token patterns
SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)(password|secret|bearer_token|api_key|access_token)\s*[:=]\s*(\S+)"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),
]


def create_blocked_remediation_plan(
    incident_id: str,
    hypothesis_id: str | None = None,
    reason: str = "Safety policy check prevented recommendation of direct changes.",
    escalation: list[str] | None = None,
    uncertainty: list[str] | None = None,
) -> RemediationPlan:
    """Build a valid, conservative BLOCKED plan requiring operator escalation."""
    return RemediationPlan(
        plan_id=f"plan-blocked-{uuid.uuid4().hex[:8]}",
        incident_id=incident_id,
        created_at=datetime.now(timezone.utc),
        recommendation_available=False,
        safety_notice=f"No production change recommended. Operator review required: {reason}",
        hypothesis_id=hypothesis_id,
        risk=RemediationRisk.BLOCKED,
        prerequisites=[],
        steps=[],
        escalation_guidance=list(escalation or [
            "Escalate to service on-call engineer for manual triage.",
            "Verify telemetry, logs, and metrics before manual intervention.",
        ]),
        unresolved_uncertainty=list(uncertainty or [
            reason,
        ]),
    )


def create_inconclusive_remediation_plan(
    context: IncidentContextSnapshot | None = None,
    incident_id: str = "unknown",
) -> RemediationPlan:
    """Build a BLOCKED plan for inconclusive investigations."""
    inc_id = incident_id
    if context and getattr(context, "incident_id", None):
        inc_id = context.incident_id

    return create_blocked_remediation_plan(
        incident_id=inc_id,
        reason="Investigation concluded with inconclusive evidence or budget exhaustion.",
        escalation=[
            "Conduct manual diagnostic inspection of logs and telemetry.",
            "Consult component owner and run diagnostic probes outside production.",
        ],
        uncertainty=[
            "Root cause could not be determined with sufficient confidence.",
            "Independent evidence support was insufficient to justify remediation.",
        ],
    )


def validate_and_sanitize_remediation_plan(
    plan: RemediationPlan,
    ranked: RankedHypothesisSet | None,
    context: IncidentContextSnapshot | None,
) -> RemediationPlan:
    """Validate plan against strict safety invariants and return sanitized plan or safe BLOCKED plan."""
    # 1. Inconclusive or missing ranking
    if (
        ranked is None
        or getattr(ranked, "status", None) != InvestigationStatus.COMPLETED
        or not getattr(ranked, "hypotheses", None)
    ):
        return create_inconclusive_remediation_plan(context, incident_id=plan.incident_id)

    top_hyp = ranked.hypotheses[0]

    # 2. Confidence check: only HIGH confidence can recommend changes
    conf_str = str(getattr(top_hyp.confidence_label, "value", top_hyp.confidence_label))
    if conf_str.lower() != ConfidenceLabel.HIGH.value.lower():
        return create_blocked_remediation_plan(
            incident_id=plan.incident_id,
            hypothesis_id=top_hyp.hypothesis_id,
            reason=f"Top hypothesis confidence is {conf_str}; only HIGH confidence qualifies for remediation recommendations.",
            escalation=[
                f"Verify symptoms with on-call owner for service '{top_hyp.affected_component}'.",
                "Collect additional diagnostics to reach definitive root-cause confidence.",
            ],
            uncertainty=top_hyp.unresolved_questions or [
                "Unresolved hypothesis uncertainty prevents recommending operational changes."
            ],
        )

    # 3. Contradicting evidence check
    if getattr(top_hyp, "contradicting_evidence", None):
        contradiction_ids = [c.evidence_id for c in top_hyp.contradicting_evidence]
        return create_blocked_remediation_plan(
            incident_id=plan.incident_id,
            hypothesis_id=top_hyp.hypothesis_id,
            reason=f"Contradicting evidence identified for top hypothesis: {', '.join(contradiction_ids)}",
            escalation=[
                "Review conflicting evidence records with systems engineers before proceeding.",
            ],
            uncertainty=[
                "Hypothesis has contradictory evidence that must be resolved.",
            ],
        )

    # 4. If plan itself is already unavailable, pass through safely
    if not plan.recommendation_available:
        return plan

    # 5. Evidence grounding check: all cited evidence must exist in hypothesis or context
    valid_evidence_ids: set[str] = set()
    if getattr(top_hyp, "supporting_evidence", None):
        valid_evidence_ids.update(c.evidence_id for c in top_hyp.supporting_evidence)
    if context:
        ev_items = getattr(context, "evidence", None) or getattr(context, "evidence_summaries", None) or []
        for s in ev_items:
            if hasattr(s, "evidence_id"):
                valid_evidence_ids.add(s.evidence_id)
            elif isinstance(s, dict) and "evidence_id" in s:
                valid_evidence_ids.add(s["evidence_id"])

    ungrounded_ids = set(plan.evidence_ids) - valid_evidence_ids
    if ungrounded_ids:
        return create_blocked_remediation_plan(
            incident_id=plan.incident_id,
            hypothesis_id=top_hyp.hypothesis_id,
            reason=f"Plan cited ungrounded evidence IDs not present in investigation context: {', '.join(sorted(ungrounded_ids))}",
            escalation=["Manually inspect evidence context to determine validated findings."],
            uncertainty=["Evidence grounding validation failed."],
        )

    # 6. Command syntax detection in steps
    for step in plan.steps:
        texts_to_check = [
            step.title,
            step.purpose,
            step.expected_result,
            *step.instructions,
            *step.verification,
            *step.rollback_guidance,
        ]
        for text in texts_to_check:
            for pattern in COMMAND_SYNTAX_PATTERNS:
                if pattern.search(text):
                    return create_blocked_remediation_plan(
                        incident_id=plan.incident_id,
                        hypothesis_id=top_hyp.hypothesis_id,
                        reason=f"Plan proposed direct shell/CLI command syntax in step '{step.title}'. Remediation must remain descriptive human guidance.",
                        escalation=["Consult approved operational runbooks for exact command syntax."],
                        uncertainty=["Automated executable syntax blocked by safety policy."],
                    )

    # 7. Secret leakage detection
    for step in plan.steps:
        texts_to_check = [
            step.title,
            step.purpose,
            step.expected_result,
            *step.instructions,
            *step.verification,
            *step.rollback_guidance,
        ]
        for text in texts_to_check:
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    return create_blocked_remediation_plan(
                        incident_id=plan.incident_id,
                        hypothesis_id=top_hyp.hypothesis_id,
                        reason="Plan contained secret or credential patterns. Output blocked for security.",
                        escalation=["Check credentials management store and rotate potentially exposed secrets."],
                        uncertainty=["Secret pattern detected in remediation instructions."],
                    )

    return plan
