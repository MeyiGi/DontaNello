# ADR 0006: Deterministic event compaction before progress analysis

Status: accepted

## Context

Progress databases can contain repeated observations. Passing every repeated row to the model makes important project changes harder to see and spends context on formatting noise. The report archive already stores original evidence, but the Groq adapter previously analyzed that raw evidence directly and could return a partial period after a later batch failed.

The configured Notion source returns current dated database rows. It does not expose prior revisions of a page, so status/property deltas between edits cannot be reconstructed from this adapter alone.

## Decision

Before analysis, a deterministic compactor groups adjacent observations for the same project and source when their normalized text is equivalent and their recorded times are no more than 15 minutes apart. It ignores whitespace and sentence punctuation while preserving identifiers, version punctuation, negation, and chronological changes. Each normalized event keeps all source evidence IDs, first/last recorded times, event type, and observation count. A project timeline points to its ordered event IDs.

The report archive stores raw period evidence and normalized events together. Groq receives normalized events, project timelines, and existing structured period reports. Monthly analysis reuses completed weekly events and the previous monthly snapshot when available; uncovered rows are compacted as a fallback. Historical event references remain resolvable to raw evidence in the archive.

Every normalized event in the current period must be included in an extraction batch before synthesis. If request or context limits prevent complete extraction, the report is not saved; internal coverage remains in logs/metrics rather than the user-facing report. Findings carry HIGH/MEDIUM/LOW confidence; LOW is excluded. A finding may reference evidence without copying a literal quote, while high-risk completion and chronological comparison rules remain guarded.

## Consequences

- Repeated polling observations reduce to a compact event with traceable source IDs.
- Distinct work separated by more than 15 minutes and any changed text remain separate observations.
- Existing archive documents decode with defaults for newly optional evidence/finding fields.
- Next steps may continue an existing project trajectory at MEDIUM confidence; model output still passes deterministic project, chronology, and result checks.
- True property-level snapshot deltas and persistent incremental ingestion are deferred until a source exposes page-version history or an explicitly archived snapshot stream. Current compaction is deterministic and repeatable per requested period, not an incremental capture service.
