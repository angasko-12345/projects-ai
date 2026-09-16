# Output — B7 Capability Resolver and Deterministic Router

> Implementation complete and suite-green. OpenCode architecture review was
> requested asynchronously and remains pending.

## What was built

- **New `agentops/routing.py`:** `AgentProfile`, `AgentCapabilityResolver`,
  `AgentRouter`, `RoutingDecision`, `RoutingAlternative`, `RoutingRejection`,
  and public `normalize_requirements`.
- **Task capabilities:** planning, architecture, implementation, debugging,
  refactoring, testing, code review, security review, documentation, and
  repository exploration.
- **Profiles:** identifier, display name, executable path, detected version,
  availability, roles, capabilities, structured-output support, cancellation
  support, timeout support, interactive/non-interactive support, configured
  priority, and optional metadata.
- **Router inputs:** role, task description, repository characteristics,
  required capabilities, available agents, user preferences, historical
  performance, exclusions, and optional capability fallback.
- **Decision outputs:** selected agent/profile, score, alternatives, reasons,
  rejected candidates, constraints, and routing mode.
- **Registry:** best-effort version detection, profile construction, and
  unchanged direct `select()` semantics plus scalar-string requirement support.
- **Workflow:** deterministic routing with exclusions and historical success
  rates; every routing attempt is persisted as a `routing.decision` event.
  `runtime.routing_enabled=false` retains static preference/fallback behavior.
- **Configuration:** additive profile fields and routing switch; bundled
  `agents.yaml` remains unchanged.
- **Documentation and exports:** README profile/routing section and package
  exports for the new public API.

## Verification

- `tests/test_routing.py`: 20 tests covering capability matching, disabled and
  unavailable agents, preference fallback, explicit preference, missing
  capabilities, deterministic scoring, explainability, disabled routing,
  registry compatibility, configuration parsing, and event persistence.
- Full suite: 309 passing, 3 environment skips.
- `git diff --check`: clean.
- Copilot snapshot review completed: one valid scalar-string compatibility
  finding was reproduced and fixed; two snapshot-packaging observations were
  rejected with evidence.
- Backup: `/tmp/agentops-backup-b7-router-20260916-182309`.
- Review snapshot: `/tmp/agentops-review-router`.

## Follow-ups

- Adjudicate the pending OpenCode architecture reply when it arrives.
- Consider whether repository characteristics should be supplied from workflow
  context beyond the current empty mapping.
- Packaging is untouched, so no executable rebuild is required.
