@/home/meyigi/.codex/RTK.md

# CodeGraph

- Use CodeGraph automatically for navigation when available. Before editing unfamiliar code, check status and explore relevant symbols. Use callers/callees or impact analysis for changes to existing symbols where relevant.
- If this project is not indexed, state that initialization is needed and use normal repository inspection.

# Persistent user preference

This is a lifelong, extensible personal automation assistant. Maintainability and incremental extension are explicit user requirements. Follow docs/architecture.md before implementing features or refactoring. The user may later extract microservices; preserve module boundaries now without introducing distributed infrastructure prematurely.

# Agent orchestration

- The primary agent owns architecture, contracts, decomposition, review, integration, checks and user communication. Use `gpt-6.1-sol` focused subagents only when independent work and separate context improve quality or time; never create a fixed swarm or mandatory verifier.
- Sol is the requested development-agent intelligence layer. Delegate bounded tasks with explicit ownership and acceptance checks; synthesize findings at root and use targeted follow-up when evidence is questionable. Do not silently switch models when unavailable.
- Development-agent delegation and deployed report analysis are distinct. Runtime reports use the existing Groq credentials and configured Groq model; do not require an OpenAI API key or native multi-agent API. Small inputs use one analysis request with bounded corrections; large periods use bounded sequential batches and final synthesis with the same citation guards. Every current fact reaches analysis; validated results are saved before stopping. Batches are not agents.
- Read docs/architecture.md. Preserve evidence, authorization and storage boundaries. Keep orchestration, progress intelligence and presentation separate.

# Architecture rules

- Python modular monolith with src layout, organized by capability, with ports and adapters at external boundaries. Keep app code in src/dontanello, tests in tests, configuration in config, documentation in docs, deployment files in deploy, helper scripts in scripts. The root assistant.py is a compatibility launcher only.
- Keep business rules independent of Notion/Telegram SDKs, HTTP payloads, filesystem, environment variables, system clock and scheduling infrastructure.
- Give each capability its own application functions, models, narrow ports and tests. Add domain classes only when domain rules justify them.
- Adapters translate external data into explicit internal types and implement ports. Define ports near the consuming capability. Prefer typing.Protocol and dataclasses when useful; ordinary functions are acceptable.
- Inject dependencies explicitly at the composition root. No global service locator, DI framework, hidden singleton clients or import-time I/O.
- Cross-capability calls use public application contracts. No imports of another capability's private implementation or direct access to its storage. Keep dependencies acyclic.
- Each capability owns its durable state and schema migrations. Infrastructure may provide connection management, but must not become a shared business repository.
- Telegram handlers, CLI and scheduled jobs delegate to application use cases; they must not duplicate business rules.
- Keep shared code small and technical. Do not move unrelated behavior into generic utils/base classes just because code looks similar.

# Clean code

- Apply SOLID proportionately: one reason to change, narrow interfaces, substitutable adapters, dependencies pointing inward, extension at real variation points.
- Apply DRY to duplicated knowledge/business rules, not superficial syntax. Apply KISS and YAGNI: use the simplest implementation meeting current requirements.
- Use Adapter, Repository, Strategy, domain events or Outbox only for a concrete need. No speculative plugin framework, universal automation engine, inheritance hierarchy or event bus.
- Prefer clear names, typed boundaries and small cohesive functions. Validate configuration at startup. Dependencies are allowed when their concrete benefit exceeds their maintenance cost.
- Separate refactoring from behavior changes where practical; preserve observable behavior unless the user requests a change.

# Reliable automation

- Define identity, retries, idempotency, time zone and restart behavior for every external side effect. Never claim exactly-once delivery across external APIs without a proven guarantee.
- Isolate job failures: one integration's outage must not disable unrelated automations. Bound network waits and retry budgets. Groq may fail over through the configured key pool only after explicit HTTP 429; count failed key attempts in the analysis budget, keep cooldown state in memory, and never log keys. Shared organization quotas and restart rechecks remain possible.
- Persist scheduled execution/delivery state before enabling recurring reports; define recovery and duplicate behavior.
- Authorization checks for Telegram must precede private reads and actions. Never log tokens or include them in committed files or test fixtures.
- Future group features must use explicit chat-specific capabilities and separate data scopes. A couple's group must never inherit personal task/work commands, Notion access or report destinations. Enforce this in application authorization before data reads; keep LLM context scoped to the authorized chat. Group support is not currently implemented.
- `.env.example` must contain empty credential fields. Before each commit/push, run `rtk proxy python3 scripts/check_secrets.py --staged` and stop immediately on any failure; never continue to commit/push after a failed secret check. Enable the local hook with `rtk git config core.hooksPath .githooks`.
- Default personal reports must explain meaningful progress and results, group repeated records by project, and distinguish activity from completion. Use the configured Groq progress analyzer; do not silently substitute a row dump or fabricate accomplishments. `/week full` and `/month full` remain explicit raw diagnostics.
- Progress reviews use the shared fact pipeline and separate report strategies. Preserve source evidence and exact citations in the scoped archive; prior generated prose is never independent evidence. Keep progress, learning and comparisons before blockers and next steps. Never turn ideas, intentions or suspected causes into completed results. When historical data is missing, state the gap; do not invent a baseline or infer a personality change. Preserve period/delivery identities when evolving report formats.
- LLMs, if added, propose actions through explicit application contracts; permission checks and execution stay deterministic. Existing user authorization governs whether confirmation is needed.

# Workflow and verification

- Before a change, read this file and docs/architecture.md, identify the owning capability and affected contracts. Do not request routine architecture approval again.
- For a meaningful change, implement behavior tests for business rules and failure/restart cases. Prefer fakes at ports over mocking implementation details. No live writes as routine tests.
- Preserve and extend tests/architecture when adding modules; documentation alone is not enforcement.
- Run relevant checks with `rtk proxy make check` (Ruff, mypy, unittest). Run tests alone with `rtk proxy make test`. Install dev tools in .venv as documented in README.md.
- Record consequential architectural decisions and reasons in docs/decisions; update architecture docs when boundaries change. Do not add an ADR for every small edit.
- Preserve `state/checkboxes.json`, its existing keys, `.env`, existing service entrypoint and configured field names during migration. Use a tested migration if formats change. Do not reset the live baseline.
- Finish with what changed, validation performed and material limitations. Distinguish implemented architecture from planned architecture.
