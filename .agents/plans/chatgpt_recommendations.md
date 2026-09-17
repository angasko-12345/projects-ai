# ChatGPT Recommendations for AgentOps

## Current revision

**Revised:** 2026-09-17  
**Repository:** `angasko-12345/projects-ai`  
**Branch:** `main`

This is the current AgentOps architecture and improvement plan.

> Historical findings are preserved in the form of current-status notes, retained design principles, acceptance criteria, and a legacy checklist near the end. Items that are now verified as implemented are marked **DONE** rather than treated as current work.

---

# 1. Executive Verdict

AgentOps has moved materially beyond the state of the original audit.

The earlier audit identified these foundations as important:

- agent-run lifecycle
- structured agent results
- verification
- failure classification
- retry and repair
- crash recovery
- SQLite persistence
- Git worktree isolation
- agent discovery/routing
- events
- artifacts
- CLI/GUI

Those foundations remain important.

Several of the biggest architectural recommendations are now verified as implemented:

- formal execution/state semantics
- AgentAdapter and capability model
- ProcessRuntime
- structured failure evidence
- storage DTO/controller serialization
- event infrastructure
- persisted worktree provenance
- structured agent results
- capability-aware routing

The project should no longer spend the next major cycle redoing those pieces.

## Current strategic direction

```text
Finish orchestration contracts
        ↓
Make review / approval / merge first-class
        ↓
Add regression/CI protection
        ↓
Build Task → Done UX
        ↓
Expose routing and recovery intelligence
        ↓
Add power-user features
```

No rewrite is justified.

---

# 2. Verified Current State

| Area | Status |
|---|---|
| Execution state machine | **DONE** |
| AgentAdapter + capability model | **DONE** |
| ProcessRuntime | **DONE** |
| Structured failure evidence | **DONE** |
| Storage DTO boundary | **DONE** |
| Typed/persisted events | **DONE** |
| Persisted worktree refs | **DONE** |
| Structured AgentResult | **DONE** |
| Capability-aware deterministic router | **DONE** |
| Worktree finalization centralization | **DONE** |
| GUI/controller boundary | **DONE** |
| Artifact registry core | **DONE / lifecycle hardening remains** |
| WorkflowEngine decomposition | **OPEN** |
| ReviewRun | **OPEN** |
| MergeRun | **OPEN** |
| Typed Git merge failures | **OPEN** |
| Approval/attention domain | **OPEN** |
| Persistence failure policy | **OPEN** |
| CI/regression gating | **OPEN** |
| StateStore repository split | **OPEN** |
| Task → Done product flow | **OPEN** |

The latest verified repository history records a real Pi AgentOps round-trip and a full suite of **357 OK** after the Pi invocation regression fix.

---

# 3. Current Architecture

The current architecture is approximately:

```text
CLI / GUI
    |
    v
AgentOpsController / application boundary
    |
    v
WorkflowEngine
    |
    +-- routing
    +-- task execution
    +-- verification
    +-- retry/repair
    +-- recovery
    |
    +--> AgentAdapter
    |       |
    |       v
    |   ProcessRuntime
    |       |
    |       v
    |   external agent CLI
    |
    +--> VerificationKernel
    |       |
    |       v
    |   ProcessRuntime
    |
    +--> GitWorktreeManager
    |
    +--> StateStore
            |
            v
          SQLite
```

The architecture is fundamentally sound.

The remaining problem is that `WorkflowEngine`, `StateStore`, and finalization still aggregate too much responsibility.

---

# 4. Execution State Model — DONE, NOW A FOUNDATION

The original recommendation called for an authoritative success/state model.

That is now implemented through `execution_model.py` and state-transition validation.

The key distinction must remain:

```text
process success
    ↓
agent result accepted
    ↓
verification passed
    ↓
review approved
    ↓
merge eligible
    ↓
merge succeeded
    ↓
workflow completed
```

These must never collapse into one boolean.

### Keep

- fail-loud invalid transitions
- verification as evidence
- recovery that never fabricates success
- invariant tests
- one authoritative semantic definition of completion

### Future use

Extend the same model to ReviewRun, Approval, and MergeRun rather than inventing new status logic in each subsystem.

---

# 5. AgentAdapter + Capability Model — DONE

The original AgentAdapter recommendation is implemented.

Current adapter responsibilities include:

- agent identity
- role support
- capability reporting
- command construction
- executable pinning/selection

Generic process behavior stays in ProcessRuntime.

### Important remaining rule

Do not create one bespoke adapter class per agent merely for architecture aesthetics.

Add a dedicated adapter only when a CLI genuinely differs in protocol or behavior.

Examples that could justify specialization:

- nonstandard structured output
- streaming protocol
- unique model selection
- special interactive lifecycle

Otherwise use the generic CLI adapter.

---

# 6. ProcessRuntime — DONE

The shared runtime now owns:

- subprocess spawning
- environment filtering
- cancellation
- timeouts
- process-group policy
- termination/cleanup
- stdout/stderr capture

Agent execution and verification now share it.

### Rule

New process-consuming code must use ProcessRuntime instead of duplicating subprocess lifecycle code.

---

# 7. Agent Result Parser

The external agent-result parser should stay tolerant.

Agent output is untrusted input.

Use two conceptual layers:

### External parser

- tolerate malformed agent output
- preserve warnings
- safely downgrade invalid structured output
- never turn malformed output into false success

### Internal validation

- fail loudly on impossible AgentOps-owned states
- enforce domain invariants
- reject impossible transitions

Do not use broad exception swallowing around AgentOps-owned invariants.

---

# 8. Verification

Verification is a first-class subsystem.

It distinguishes:

```text
VerificationCheck
VerificationRun
VerificationReport
```

and supports tests, linting, formatting, type checks, builds, custom checks, sequential/parallel execution, and fail-fast/continue behavior.

### Security boundary

Keep:

- no shell execution
- configured commands only
- working-directory containment
- explicit timeout
- cancellation
- persistent evidence

Verification must not depend on AgentRunner internals. It should depend on ProcessRuntime.

---

# 9. Failure Kernel — STRUCTURED EVIDENCE DONE

Failure categories include:

```text
AGENT_ERROR
PROCESS_ERROR
TIMEOUT
CANCELLATION
TEST_FAILURE
LINT_FAILURE
TYPECHECK_FAILURE
BUILD_FAILURE
VERIFICATION_FAILURE
ENVIRONMENT_FAILURE
DEPENDENCY_FAILURE
GIT_CONFLICT
DIRTY_WORKTREE
POLICY_VIOLATION
REVIEW_REJECTION
UNKNOWN
```

Retry and repair actions remain distinct.

Structured `FailureEvidence` is now the preferred classification input, with text matching as fallback.

### Remaining improvement

Build a human-readable failure-explanation service on top of the structured evidence.

The UI should not parse stderr on its own.

---

# 10. Retry vs Repair

Keep this distinction explicit.

```text
Retry
= repeat execution with essentially the same context

Repair
= intentionally modify implementation/context before another attempt
```

Persist lineage so a task can be understood as:

```text
Attempt 1
   ↓
Retry
   ↓
Repair cycle
   ↓
Attempt 2
```

Do not turn repair into a renamed retry.

---

# 11. WorkflowEngine — MAIN REMAINING ARCHITECTURAL HOTSPOT

The engine still combines:

- dependency planning
- scheduling
- claiming
- routing
- execution
- verification
- retries
- repairs
- failure recording
- recovery
- cancellation
- workflow completion

### Target

Keep the public facade:

```text
WorkflowEngine
```

Extract:

```text
WorkflowPlanner
WorkflowScheduler
TaskExecutor
RepairCoordinator
RecoveryCoordinator
```

### Acceptance criteria

- public WorkflowEngine API remains compatible
- dependency graph validation is isolated
- ready-task/concurrency logic is isolated
- task execution is isolated
- repair/retry policy is isolated
- crash recovery is isolated
- full suite remains green

This is a behavior-preserving refactor, not a rewrite.

---

# 12. ReviewRun / MergeRun — TOP DOMAIN GAP

Review and merge are not yet first-class persisted lifecycle objects.

Add:

```text
ReviewRun
MergeRun
```

## ReviewRun

Track:

- workflow/task
- source run/commit
- reviewer
- status
- findings
- decision
- evidence references
- timestamps
- failure

Suggested states:

```text
PENDING
RUNNING
PASSED
REJECTED
FAILED
CANCELLED
```

## MergeRun

Track:

- workflow
- base branch
- base commit
- source branch
- source commit
- verification references
- review references
- approval references
- result commit
- status
- failure reason
- timestamps

Suggested states:

```text
PENDING
RUNNING
MERGED
CONFLICT
BLOCKED
FAILED
CANCELLED
```

AgentOps should be able to answer:

```text
What exact commit was reviewed?
What exact commit was verified?
What base was merged into?
What approved it?
Why did merge fail?
What happened afterward?
```

---

# 13. Git Finalization — MUST BE CORRECTED

`finalize_worktree()` still treats generic `GitError` during merge as a merge conflict.

That incorrectly conflates:

```text
BASE_DIRTY
BASE_BRANCH_CHANGED
BASE_COMMIT_CHANGED
MERGE_CONFLICT
GIT_COMMAND_FAILED
REPOSITORY_INVALID
```

Make these typed failure reasons.

Only actual conflicts should create conflict-resolution work.

This should become part of MergeRun and the failure/recovery kernel.

---

# 14. Git as the Authoritative Change Record

Telemetry such as:

```text
git status
git diff --stat
```

is useful but observational.

The authoritative chain should become:

```text
base commit
    ↓
isolated branch
    ↓
agent commit
    ↓
verification
    ↓
review
    ↓
approval
    ↓
merge
    ↓
result commit
```

Persist references to this Git graph.

Do not make UI-generated summaries the source of truth for code changes.

---

# 15. StateStore — LONG-TERM REFACTOR

StateStore currently combines:

- connection setup
- schema/migrations
- reconstruction
- workflows
- tasks
- agent runs
- verification
- failures
- events
- artifacts
- recovery

Keep the existing StateStore facade.

Gradually extract:

```text
WorkflowRepository
TaskRepository
RunRepository
VerificationRepository
FailureRepository
EventRepository
ArtifactRepository
ReviewRepository
MergeRepository
ApprovalRepository
```

This should be a mechanical extraction with no destructive migration.

---

# 16. Persistence Failure Policy — OPEN

Not every persistence failure should be treated the same.

Classify operations as:

```text
SAFE_TO_DEGRADE
BEST_EFFORT_WITH_WARNING
MUST_FAIL_CLOSED
```

Critical transitions such as:

```text
workflow completed
verification passed
approval recorded
merge succeeded
```

should fail closed if the system cannot persist the evidence required to prove the state.

Telemetry can be best-effort.

A persistence outage must never fabricate a successful terminal state.

---

# 17. Artifact Lifecycle — PARTIAL

Artifact persistence and orphan helpers exist.

Finish the lifecycle:

```text
CREATED
ATTACHED
VERIFIED
PRESERVED
CLEANED
ORPHANED
```

Every artifact should answer:

```text
Who created it?
Which run?
Which task?
Which workflow?
Which worktree?
Is it referenced?
Can it be safely deleted?
```

Cleanup must respect unresolved failures and preserved worktrees.

---

# 18. Agent Routing — BACKEND DONE, PRODUCT EXPOSURE OPEN

Capability-aware deterministic routing exists.

Routing decisions can contain:

- selected agent
- alternatives
- reasons
- rejected candidates
- constraints

Expose this through the product as:

```text
Agent: Auto

Selected: OpenCode

Why:
Best available implementation fit

Alternatives:
Pi
Codex
```

Users must be able to override Auto.

Do not expose raw numerical scores as a misleading quality rating.

---

# 19. Configuration

The configuration system is intentionally lightweight and validated.

The long-term concern is prompt argument substitution: different CLIs may have different command protocols.

AgentAdapter is now the correct place for command-shape differences.

Do not add more CLI-specific command construction to WorkflowEngine.

---

# 20. CLI

The CLI already exposes a meaningful operational surface:

```text
agents
run
status
runs
task
logs
verify
failures
recover
events
artifacts
workflow
gui
```

Keep it thin:

```text
parse
  ↓
application/controller service
  ↓
render
  ↓
exit code
```

Do not move orchestration semantics into CLI rendering code.

Future commands such as:

```text
doctor
approve
reject
review
merge
```

should also call application services.

---

# 21. GUI / Product Direction

The GUI currently functions primarily as an operator console with:

- direct agent run
- task workflow
- history
- logs
- artifacts
- worktrees
- workflow status

The next step is not "more tabs".

The next step is a product layer built around:

```text
Task
 ↓
Progress
 ↓
Verification
 ↓
Failure/Repair
 ↓
Review
 ↓
Approval
 ↓
Change
 ↓
Merge
 ↓
Done
```

See `chatgpt_addition_recommendations.md` for the complete 29-feature UX/product plan.

---

# 22. Testing Strategy

The project already has broad subsystem tests.

The next stage should emphasize invariants and real round-trips.

### Agent invariants

- RUNNING cannot become PENDING
- terminal states cannot become active
- completed runs have consistent terminal evidence

### Verification invariants

- required failed checks prevent PASS
- cancellation cannot become PASS
- empty/invalid evidence cannot fabricate success

### Workflow invariants

- dependencies resolve before dependents
- failed required verification blocks downstream success
- recovery cannot invent success

### Git invariants

- dirty base blocks merge
- base branch drift blocks merge
- base commit drift blocks merge
- actual conflict never becomes success

### Persistence invariants

- migrations are idempotent
- recovery is idempotent
- state survives restart

### Integration tests

Add real round-trips for supported agent CLIs where environment permits.

---

# 23. CI / Regression Gating — OPEN

Continuous CI should be added.

Minimum:

```text
python -m unittest discover -s tests
compile check
lint/static checks where configured
```

Packaging should be smoke-tested whenever packaging changes.

The exact current suite count must not be hardcoded in docs unless a current commit verifies it.

---

# 24. Security Model

Keep these properties:

- subprocess execution without shell
- restricted environment
- prompt redaction
- worktree containment
- verification containment
- `GIT_TERMINAL_PROMPT=0`
- explicit verification commands
- persistent evidence
- conservative crash recovery

Important boundary:

> AgentOps is an orchestrator, not a complete sandbox.

The coding agent remains a powerful local process with whatever permissions the host environment gives it.

If stronger sandboxing is desired later, treat that as a dedicated project.

---

# 25. Recommended 0.2 Architecture

Directional target:

```text
Interfaces
    |
Application / Controller
    |
Workflow Facade
    |
+---+---+---+---+
|   |   |   |   |
Planner
Scheduler
Executor
Repair
Recovery
    |
+---+------------------+
|                      |
AgentAdapter       Verification
|                      |
+----------+-----------+
           |
     ProcessRuntime
           |
        processes

Persistence facade
    |
domain repositories
    |
SQLite

Git / Worktree
    |
ReviewRun
Approval
MergeRun

Events / Artifacts
```

Do not reorganize the repository merely to make directory names match this diagram.

---

# 26. Current Priority Roadmap

## P1 — Kernel completion

1. Decompose WorkflowEngine
2. Add ReviewRun
3. Add MergeRun
4. Type Git finalization failures
5. Add Approval/Attention domain
6. Add CI/regression gating

## P2 — Durability

7. Persistence failure policy
8. Complete artifact lifecycle
9. StateStore repository split
10. Failure explanation service

## P1 — Product vertical slice

11. New Task
12. Mission Control
13. Live Activity
14. Run Timeline
15. Failure Explanation
16. Diff Viewer
17. Approval / Inbox
18. Retry / Repair actions

## P2 — Intelligence UX

19. Auto routing UI
20. Agent Profiles
21. Project onboarding
22. Verification presets
23. AgentOps Doctor
24. Recovery Center

## P3 — Power features

25. Run Replay
26. Global Search
27. Cost/resource tracking
28. Budgets
29. Analytics
30. Visual workflow builder

---

# 27. What Not to Do

Do not:

- rewrite AgentOps
- create a second workflow engine
- duplicate SQLite state in the GUI
- hardcode agent-specific logic into WorkflowEngine
- expose chain-of-thought
- treat every failure as generic
- collapse retry and repair
- call every Git error a merge conflict
- add dozens of integrations before the adapter contract proves useful
- build the visual editor before the workflow model is mature
- add speculative distributed/cloud execution before local correctness is excellent
- build a giant plugin marketplace before the integration boundary is stable
- make the dashboard a collection of raw database tables

---

# 28. Design Principles

## 1. Agent success is not workflow success

An agent claiming "done" is not proof.

## 2. Verification is evidence

Configured verification is authoritative evidence.

## 3. Never fabricate success during recovery

Recovery produces unresolved/failed states unless fresh evidence proves success.

## 4. Git is authoritative for actual changes

Use commit identity whenever possible.

## 5. The orchestrator is the conductor

AgentOps coordinates agents; it should not become another monolithic coding agent.

## 6. Keep agent-specific knowledge behind adapters

CLI quirks do not belong in the core.

## 7. Prefer structured evidence

Strings are fallback signals.

## 8. Persistence is part of correctness

Critical state must survive restart and must not claim success without durable evidence.

---

# 29. Acceptance Criteria

## Orchestration

- WorkflowEngine is a compatibility facade.
- Planner/scheduler/executor/repair/recovery responsibilities are separable.
- Retry and repair lineage remains correct.
- Cancellation and recovery remain deterministic.

## Review/Merge

- ReviewRun and MergeRun persist.
- Exact source/base/result commits are recorded.
- Merge failure reasons are typed.
- A conflict is never fabricated from an unrelated Git error.

## Approval

- approval requests persist
- decisions are auditable
- critical approval transitions fail closed when persistence is unavailable

## Durability

- CI protects the suite
- artifact cleanup is safe
- persistence degradation is visible

## UX

- one natural-language task can start an appropriate workflow
- progress is understandable
- failures are explained
- repair/retry are simple
- exact changes are visible
- important actions can require approval
- final status is unambiguous

---

# 30. Bottom Line

AgentOps already has the hard machinery.

The project now needs a stronger center around:

```text
Workflow
 → Task
   → AgentRun
   → VerificationRun
   → Failure / Repair
   → ReviewRun
   → Approval
 → MergeRun
```

The biggest remaining engineering risks are:

1. WorkflowEngine becoming harder to change
2. Review/merge/approval remaining second-class operations
3. Git finalization conflating distinct failures
4. silent/degraded persistence at critical transitions
5. incomplete artifact lifecycle semantics
6. lack of CI gating
7. a product UI that exposes internals instead of hiding them

The biggest product opportunity is:

```text
Task → Done
```

The system should decide how to execute the task, show progress, verify the result, recover safely, request human attention only when needed, show exact changes, and merge only when the required evidence exists.

---

# 31. Retained Historical Findings

The following earlier findings remain valid and are intentionally retained:

- Agent result parsing should separate tolerant external parsing from strict internal validation.
- AgentRun prompt metadata should never persist raw prompts.
- Run lineage should remain explicit for retry and repair.
- Verification should stay independent from agent success.
- Retry and repair must remain separate semantics.
- Git should remain the authoritative change record.
- StateStore should eventually split behind stable repositories.
- CLI should remain a thin interface.
- GUI should remain a presentation/client layer.
- Agent-specific knowledge belongs behind adapters.
- Structured failure evidence is better than string-first classification.
- ProcessRuntime should be the shared subprocess boundary.
- Recovery must never fabricate success.
- Worktree isolation and stored base provenance are core safety mechanisms.
- Artifact retention must respect unresolved failures.
- Future APIs should consume application/domain services rather than directly manipulating persistence.
- Visual workflow tools must represent the same workflow model rather than inventing another engine.
- The project should evolve incrementally, not through a rewrite.

---

# 32. Historical Recommendation Status Ledger

| Original recommendation | Current status |
|---|---|
| Formal execution state machine | **VERIFIED DONE** |
| AgentAdapter | **VERIFIED DONE** |
| ProcessRuntime | **VERIFIED DONE** |
| Structured failure evidence | **VERIFIED DONE** |
| Storage DTOs | **VERIFIED DONE** |
| Event bus | **VERIFIED DONE** |
| Persisted worktree refs | **VERIFIED DONE** |
| Structured agent results | **VERIFIED DONE** |
| Artifact registry | **VERIFIED DONE; lifecycle remains** |
| Capability router | **VERIFIED DONE** |
| WorkflowEngine decomposition | **STILL OPEN** |
| ReviewRun/MergeRun | **STILL OPEN** |
| StateStore split | **STILL OPEN** |
| Persistence failure policy | **STILL OPEN** |
| CI gating | **STILL OPEN** |
| Product UX layer | **STILL OPEN / partially surfaced** |

This ledger is the current replacement for the older roadmap ordering.

---

# 33. Immediate Next Work

The recommended next implementation slice is:

```text
1. WorkflowEngine decomposition plan
2. ReviewRun domain + persistence
3. MergeRun domain + typed Git failures
4. Approval/Attention domain
5. CI gate
6. New Task → Run → Verify → Review → Approve → Merge vertical slice
```

Everything else should build on those contracts.

