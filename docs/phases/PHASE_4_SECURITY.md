# Phase 4 Security, Provenance, and Quality Boundary

## Boundary guarantee

`EvidenceSecurityService` is the only supported transition from Phase 3's
pre-security candidates to persistence-eligible `SecuredEvidenceCandidate`
values. It does not persist or deduplicate evidence. Every approved value has
complete provenance and quality metadata, and contains no original raw payload.

The service hashes canonical raw bytes first and releases that temporary byte
representation immediately. It then recursively evaluates the summary,
attributes, warnings, identity text, and raw payload. A record either passes,
is returned with redactions, or is represented only by a content-free
`QuarantinedRecord`.

## Redaction policy

`RedactionPolicy` is replaceable. The reference `PatternRedactionPolicy` and
`Redactor` cover:

- bearer/access/refresh tokens and API keys;
- password and secret-valued fields;
- database connection URLs;
- PEM private-key blocks;
- common AWS, Azure, and Google credential fields;
- secret-valued environment variables;
- configured sensitive fields and personal identifiers;
- sensitive text in nested keys and values; and
- URL-encoded or base64-encoded values that decode to a recognized pattern.

Redaction paths contain sanitized keys, not original sensitive key text.
Private-key material is quarantined by default. This is configurable, but no
policy-rejected content is placed in the approved result.

## Provenance

`ProvenanceBuilder` uses the injected `PayloadHasher` port. Its reference is the
existing SHA-256 adapter, and the digest includes its algorithm prefix. Hashing
uses canonical bytes, so semantically identical JSON objects produce the same
digest regardless of object-key order. Batch, query, source-record, and adapter
identity remain attached after redaction.

## Quality policy

`QualityPolicy` is replaceable. `ConfigurableQualityPolicy` selects reliability
from explicit evidence-type rules, then source-type rules, then a configured
default. No vendor receives an intrinsic reliability assumption. Optional
truncation degradation moves high to medium or medium to low and records the
decision in a human-readable rationale.

Freshness uses `observed_at`, falling back to `event_time`. The reference point
is configured as either batch `collected_at` or an injected clock. Missing time
produces unknown freshness. Future timestamps are clamped to zero and create a
sanitized clock-skew warning.

## Failure behavior

Sensitive provenance identifiers fail closed into quarantine instead of being
silently rewritten. Invalid policy output also quarantines. Hash-adapter
exceptions become `EvidenceSecurityError` with a fixed message, preventing a
backend exception from reflecting payload data. Quarantine warnings and
results contain only identifiers, a digest, and fixed reason codes.

`secure_batch` also sanitizes the normalization result's batch-level warnings
and source errors. If a warning or error itself contains quarantinable content,
the original issue is omitted and replaced with a fixed policy message.
