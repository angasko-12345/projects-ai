# Archived task record — repository characteristics for routing (2026-10-23)

> Preserved from `tasks/task.md` (2026-10-23, after completion): the plan is as written
> while the task ran; the Status line was updated to complete at archive time.
> Do not re-execute. The full output lives in
> [`../../outputs/repo-characteristics-routing.md`](../../outputs/repo-characteristics-routing.md);
> the decision entry in `.agents/memory/decisions.md` (2026-10-23); the queue row in
> `.agents/pending_tasks.md` is marked ✅.

## Task — repository characteristics for routing (AgentOps, 2026-10-23, OpenCode/GLM)

The audit (pending_tasks row "Repository characteristics for routing are inert") found
`agentops/workflow.py:_select_agent` passing `repository_characteristics={}` into
`AgentRouter.route`, so the mapping `AgentCapabilityResolver.required_capabilities`
reads at `routing.py:431` was always empty — every repository-derived routing signal
was dead input.

### Plan

1. New leaf `agentops/repo_inspection.py`: deterministic, bounded, name-based
   inspection (dominant languages by extension, root manifests, test runner,
   size category; bounded walk + bounded cache; never opens `.env` or any
   credential file; never raises).
2. Wire `WorkflowEngine._select_agent` to pass derived flags from the task's
   working directory; explicit preferences, required capabilities, exclusions,
   and the static fallback keep their existing semantics.
3. Unit tests (`tests/test_repo_inspection.py`): empty/unknown, mixed-language,
   inaccessible paths, consistency, caching, no-credential-read proof.
4. Routing tests proving one real decision changes via a reasoned signal
   (`requires_tests`), while explicit user preference and the disabled-router
   static fallback still work, and `working_directory=None` keeps `{}`.
5. Run the AgentOps suite from `agentops/`; update the queue row and memory.

### Status: complete (see `.agents/outputs/repo-characteristics-routing.md`)

Executed as planned; every step done. Verification: `cd agentops && python -m unittest
discover -s tests` — 876 tests, 4 skipped, OK (exit code 0), twice observed. Uncommitted
(no authorization).
