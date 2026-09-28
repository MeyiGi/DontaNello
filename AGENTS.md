@/home/meyigi/.codex/RTK.md

# CodeGraph

- Use CodeGraph automatically for navigation when available. Before editing unfamiliar code, check status and explore relevant symbols. Use callers/callees or impact analysis for changes to existing symbols where relevant.
- If this project is not indexed, state that initialization is needed and use normal repository inspection.

# Persistent user preference

This is a lifelong, extensible personal automation assistant. Maintainability and incremental extension are explicit user requirements. Follow docs/architecture.md before implementing features or refactoring. The user may later extract microservices; preserve module boundaries now without introducing distributed infrastructure prematurely.

# Agent orchestration

- The primary agent remains the master/orchestrator and delegates suitable subtasks to `gpt-6-luna` agents with `reasoning_effort="high"`. Delegate substantive, separable work by default; trivial one-step edits may stay inline.
- The primary agent owns architecture, contracts, task decomposition, review, integration, final checks and user communication. Delegate bounded investigation, implementation, tests or review; avoid conflicting concurrent file ownership. Run dependent tasks sequentially and independent tasks in parallel within available slots.
- Spawn Luna agents with `model="gpt-6-luna"`, `reasoning_effort="high"`, and `fork_turns="none"` or selective numeric context; model overrides are not supported with `fork_turns="all"`. Give each agent explicit task, context, files, contracts and acceptance checks.
- Agents follow this file and `docs/architecture.md`. The primary agent verifies diffs and required checks rather than relying only on agent reports. If Luna is unavailable, do not silently switch to another model; disclose this and continue inline where appropriate.
- This is a development workflow only, not a multi-agent feature in the deployed assistant runtime.

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
- Isolate job failures: one integration's outage must not disable unrelated automations. Bound network waits and retry budgets.
- Persist scheduled execution/delivery state before enabling recurring reports; define recovery and duplicate behavior.
- Authorization checks for Telegram must precede private reads and actions. Never log tokens or include them in committed files or test fixtures.
- LLMs, if added, propose actions through explicit application contracts; permission checks and execution stay deterministic. Existing user authorization governs whether confirmation is needed.

# Workflow and verification

- Before a change, read this file and docs/architecture.md, identify the owning capability and affected contracts. Do not request routine architecture approval again.
- For a meaningful change, implement behavior tests for business rules and failure/restart cases. Prefer fakes at ports over mocking implementation details. No live writes as routine tests.
- Preserve and extend tests/architecture when adding modules; documentation alone is not enforcement.
- Run relevant checks with `rtk proxy make check` (Ruff, mypy, unittest). Run tests alone with `rtk proxy make test`. Install dev tools in .venv as documented in README.md.
- Record consequential architectural decisions and reasons in docs/decisions; update architecture docs when boundaries change. Do not add an ADR for every small edit.
- Preserve `state/checkboxes.json`, its existing keys, `.env`, existing service entrypoint and configured field names during migration. Use a tested migration if formats change. Do not reset the live baseline.
- Finish with what changed, validation performed and material limitations. Distinguish implemented architecture from planned architecture.
