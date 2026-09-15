# ChatGPT Recommendations for AgentOps

## Scope

This document captures the actionable findings, recommendations, priorities, architectural direction, and final assessment from a full static audit of the AgentOps codebase.

Repository audited:

- `angasko-12345/projects-ai`
- AgentOps implementation under `agentops/`

No repository files were modified during the audit.

> Audit note: GitHub file-fetch output truncated some large source files, so this is a deep static audit of the core implementation and test inventory, not a claim that the entire test suite was executed locally.

---

# 1. Executive Verdict

AgentOps is substantially more mature architecturally than a typical early-stage orchestration project.

It already contains the foundations of a real local coding-agent orchestration platform:

- Persistent agent-run lifecycle tracking
- Structured agent results
- Deterministic verification
- Failure classification
- Retry and repair planning
- Crash recovery
- SQLite persistence
- Git worktree isolation
- Agent discovery and role-based routing
- Event persistence
- Artifact handling
- CLI and GUI interfaces
- A broad test suite covering major subsystems

The biggest issue is not a lack of features. The bigger issue is that several strong subsystems have evolved independently and now need stronger central contracts.

The project is approaching the point where architectural consolidation and contract stabilization will provide more value than continuously adding features.

### Overall assessment

| Area | Rating |
|---|---:|
| Domain modeling | 9/10 |
| Agent lifecycle | 8.5/10 |
| Verification | 9/10 |
| Failure/recovery | 8.5/10 |
| Git isolation | 8.5/10 |
| Persistence | 8/10 |
| Configuration | 8/10 |
| Agent abstraction | 6.5/10 |
| Workflow engine | 7/10 |
| Security boundaries | 7.5/10 |
| Test coverage/design | 8/10 |
| Overall architecture | **8/10** |

The current system is strong enough that a rewrite is not justified. The right move is targeted evolution around a stronger orchestration kernel.

---

# 2. Architecture Summary

The current architecture broadly looks like:

```text
CLI / GUI
   |
   v
WorkflowEngine
   |
   +-- AgentRegistry
   |      +-- AgentConfig
   |
   +-- AgentRunner
   |      +-- subprocess execution
   |
   +-- VerificationKernel
   |      +-- subprocess verification
   |
   +-- FailureClassifier
   |
   +-- GitWorktreeManager
   |
   +-- StateStore
          +-- SQLite
```

This is fundamentally sound.

One of the best design choices is the use of separate domain models for things that have genuinely different semantics: agent runs, verification runs, tasks, failures, and workflow state.

The main architectural problem is that the orchestration layer still has to translate between these concepts manually in several places. As the project grows, this will become the main source of complexity.

---

# 3. Strongest Parts of the Existing Codebase

## 3.1 Explicit Agent Run Lifecycle

The agent run model distinguishes:

```text
PENDING
STARTING
RUNNING
COMPLETED
FAILED
CANCELLED
TIMED_OUT
TERMINATED
```

This is appropriate for a persistent execution system.

The project also explicitly identifies terminal states instead of relying on ad-hoc checks.

### Why this matters

A durable orchestration system needs to distinguish:

- process started but never finished
- process finished normally
- process failed
- process was cancelled
- process timed out
- process was terminated

That foundation is already present.

---

## 3.2 Prompt Privacy and Safe Run Metadata

The `AgentRunContext` does not persist the raw prompt.

Instead it records:

- prompt hash
- prompt length
- no prompt preview

Command metadata also redacts the raw prompt.

This is a strong security and privacy decision because coding-agent prompts can contain credentials, repository details, private paths, or other sensitive content.

### Recommendation

Keep this design.

Do not weaken prompt redaction just to make diagnostics easier. Improve diagnostics with structured metadata instead.

---

## 3.3 Run Lineage

Agent runs support relationships such as:

```text
ROOT
RETRY
REPAIR
```

and IDs such as:

```text
parent_run_id
retry_of
repair_of
```

This enables execution history such as:

```text
Task
 |
 +-- Run #1
 |    +-- FAILED
 |
 +-- Retry #2
 |    +-- FAILED
 |
 +-- Repair #3
      +-- PASSED
```

This is a very useful foundation for debugging, GUI history, and future automation.

### Recommendation

Preserve the lineage model and eventually expose it as a first-class execution graph in the GUI and CLI.

---

## 3.4 Structured Agent Results

The project correctly avoids treating natural-language output as authoritative success.

`AgentResult` is a versioned schema with fields for:

- status
- summary
- files changed
- test counts
- verification results
- review findings
- follow-up requests
- confidence
- errors
- warnings
- metadata

The conceptual distinction between:

```text
process success
agent success
verification success
review approval
merge eligibility
```

is especially strong.

### Recommendation

Make this distinction even more central. It should eventually become part of a formal orchestration state machine rather than just a design principle.

---

# 4. Agent Result Parser Recommendations

The result parser is intentionally defensive and handles malformed input without bringing down a successful process execution.

That is good for external, untrusted agent output.

However, the parser contains many broad exception handlers. That creates a long-term risk of hiding actual AgentOps programming bugs.

## Recommended design

Maintain two conceptual layers:

### External tolerant parsing

Used for agent-produced data.

Goals:

- never crash on malformed agent output
- downgrade bad data safely
- preserve warnings and evidence

### Internal strict validation

Used for AgentOps-owned data and invariants.

Goals:

- fail loudly on programmer errors
- detect impossible state transitions
- avoid silently accepting broken internal objects

### Bottom line

Do not remove resilience. Narrow the boundaries where broad exception handling is used.

---

# 5. Runner Audit

`runner.py` is operationally important and contains several good choices.

## Strong points

- `create_subprocess_exec` rather than shell-based execution
- environment filtering
- cancellation support
- timeout handling
- process-group handling
- stdout/stderr capture
- run persistence hooks
- structured output parsing
- Windows-specific process cleanup

These are all appropriate for a local coding-agent runner.

---

## Main weakness: too much responsibility

`AgentRunner` currently deals with many concerns:

- process lifecycle
- agent command construction
- environment filtering
- cancellation
- timeout handling
- logging
- persistence hooks
- metadata collection
- structured-result parsing
- outcome construction

That is too much long-term responsibility for one class.

### Recommended decomposition

Introduce a shared process runtime:

```text
ProcessRuntime
    + spawn
    + communicate
    + cancellation
    + timeout
    + termination
    + environment policy
    + process-group management
```

Then make these layers use it:

```text
AgentRunner ----------+
                      |
VerificationKernel ----+--> ProcessRuntime
```

This avoids the current situation where the verification subsystem reuses internal methods from the agent runner.

---

# 6. Biggest Architectural Gap: AgentAdapter

This is the highest-priority structural improvement.

The current abstraction is mostly based on:

```text
name
command
args
roles
timeout
enabled
model
```

That works for basic CLI execution, but it is too weak for a multi-agent orchestration platform.

Different agent CLIs can have different capabilities and protocols.

Examples:

```text
structured output support
JSON output support
streaming support
MCP support
model selection
system prompt support
non-interactive mode
cancellation support
planning mode
review mode
read-only mode
```

## Recommended interface

Introduce an explicit `AgentAdapter` abstraction.

Conceptually:

```text
AgentAdapter
    name
    detect()
    capabilities()
    build_command()
    build_environment()
    parse_output()
    supports(role)
```

Then implementations can exist for individual tools:

```text
PiAdapter
CodexAdapter
OpenCodeAdapter
AntigravityAdapter
...
```

The workflow engine should not need to know how a particular CLI wants prompts, flags, model selection, or output formatting handled.

### Priority

**P1 / highest architectural priority**

---

# 7. Verification Model

The verification model is one of the strongest parts of the system.

The project distinguishes:

```text
VerificationCheck
VerificationRun
VerificationReport
```

rather than reducing verification to one subprocess call.

It supports:

- tests
- linting
- formatting
- type checking
- builds
- custom checks
- sequential checks
- parallel checks
- fail-fast mode
- continue-on-failure mode

This is already a real verification kernel.

---

# 8. Verification Security Boundary

The verification system has strong security-oriented choices:

- configured commands only
- no shell execution
- working-directory containment
- explicit timeouts
- cancellation handling
- persisted check state

This should remain.

## Important architectural issue

The verification kernel currently relies on internals from the agent runner, such as shared process behavior and environment handling.

That coupling should be removed.

### Recommended target

```text
                     +--> AgentRunner
ProcessRuntime -----+
                     +--> VerificationKernel
```

Both should share process infrastructure, not one depend on the other.

### Priority

**P1**

---

# 9. Failure Kernel

The failure domain is a major strength.

It distinguishes categories such as:

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

It also supports actions such as:

```text
RETRY_SAME_AGENT
RETRY_DIFFERENT_AGENT
REPAIR_IMPLEMENTATION
RERUN_VERIFICATION
REQUEST_APPROVAL
STOP
```

This is much stronger than a simple generic retry mechanism.

---

## Main weakness: string-heavy classification

The classifier relies heavily on matching strings such as:

```text
pytest
ruff
mypy
permission denied
merge conflict
```

This is practical but fragile.

A better long-term design is to classify based on structured evidence first.

Example:

```text
FailureEvidence
    source = VERIFICATION
    check_class = TESTS
    exit_code = 1
    timed_out = false
    cancelled = false
    stderr = ...
```

Then text matching becomes fallback behavior rather than the primary signal.

### Priority

**P2**

---

# 10. Retry vs Repair

The project already distinguishes retry and repair, which is good.

Make the distinction explicit:

```text
Retry
= repeat execution with essentially the same task/context

Repair
= intentionally modify implementation or execution context before another attempt
```

This distinction should be reflected consistently in task state, run lineage, repair cycles, and reporting.

Recommended conceptual model:

```text
Task
 |
 +-- Attempt 1
 |
 +-- Retry
 |
 +-- Repair cycle 1
      |
      +-- Attempt 2
```

This will make future debugging and metrics much clearer.

---

# 11. Workflow Engine Audit

`WorkflowEngine` is capable and well-designed in several important ways.

## Strong points

- dependency-aware scheduling
- explicit cycle detection
- task claiming before execution
- configurable concurrency
- agent fallback
- verification integration
- failure recording
- recovery integration
- retry and repair policy

The dependency graph logic is especially good because invalid dependency graphs are rejected before execution.

The task claiming mechanism also protects against two concurrent workers executing the same task.

---

## Main weakness: WorkflowEngine is becoming a god object

It now knows about:

- planning
- scheduling
- task execution
- agents
- verification
- failures
- retries
- repairs
- recovery
- persistence
- cancellation

That is too much long-term responsibility.

### Recommended decomposition

Split the logic conceptually into:

```text
WorkflowPlanner
WorkflowScheduler
TaskExecutor
RepairCoordinator
RecoveryCoordinator
```

Potential future structure:

```text
WorkflowEngine
    +-- Planner
    +-- Scheduler
    +-- TaskExecutor
    +-- Verification
    +-- Repair
    +-- Recovery
```

A facade can remain so the public API stays simple.

### Priority

**P1**

Do this before the engine grows substantially larger.

---

# 12. StateStore Audit

SQLite persistence is thoughtfully implemented.

Positive features include:

- WAL mode
- foreign keys
- busy timeout
- lock protection
- initialization retries
- additive migrations
- indexes
- persistent events
- agent-run persistence
- verification persistence
- failure persistence
- artifact persistence
- recovery support

The additive migration approach is appropriate for a long-lived desktop tool.

---

## Main weakness: StateStore is becoming another god object

It now combines:

```text
database connection
schema migration
object reconstruction
workflow queries
run persistence
verification persistence
failure persistence
event persistence
artifact persistence
recovery persistence
```

### Recommended long-term structure

```text
Database
MigrationManager
WorkflowRepository
TaskRepository
RunRepository
VerificationRepository
FailureRepository
EventRepository
ArtifactRepository
```

Keep a `StateStore` facade if desired, but move domain-specific persistence logic into repositories.

### Priority

**P2**

This is important, but less urgent than AgentAdapter and WorkflowEngine decomposition.

---

# 13. Git Worktree System

Git isolation is one of the strongest operational parts of AgentOps.

The system creates isolated branches and worktrees under:

```text
.agentops/worktrees/
```

It records:

- repository
- branch
- base branch
- base commit
- worktree path

Before merging, it verifies that:

- the base worktree is clean
- the base branch has not changed
- the base commit has not changed

This is exactly the sort of protection needed for concurrent coding-agent execution.

---

## Recommended improvement: first-class MergeRun

Agent runs and verification runs are first-class persistent operations.

Git merging is currently less explicit in the domain model.

Introduce a future `MergeRun` concept:

```text
PENDING
RUNNING
MERGED
CONFLICT
FAILED
```

That would make merge crashes and merge recovery easier to reason about.

Eventually the system should be able to answer:

```text
Which exact verified commit was merged?
Which base commit was merged into?
Which verification run approved it?
Did merge conflict?
What happened after conflict?
```

### Priority

**P1/P2**

---

# 14. Authoritative Change Record

The current `GitRunMetadataCollector` is good telemetry, but it should not become the canonical source of change information.

Commands such as:

```text
git status

git diff --stat HEAD
```

are observational.

The stronger future chain is:

```text
base commit
    |
    v
agent branch
    |
    v
agent commit
    |
    v
verification
    |
    v
approved commit
    |
    v
merge
```

The Git object graph should eventually serve as the authoritative record of what changed and what was merged.

Telemetry can still record helpful summaries around it.

---

# 15. Agent Registry

The agent registry is simple and clean.

Current behavior is roughly:

```text
preferred agents
    -> remaining agents
    -> available agents
    -> role compatibility
```

This is easy to understand and appropriate for the current stage.

## Recommended evolution

Move from simple role-based configuration toward capability-aware selection.

For example:

```text
agent capabilities:
    coding
    review
    planning
    structured_output
    streaming
    mcp
    model_selection
    read_only
```

Then the registry can select based on actual task requirements.

---

# 16. Configuration

The configuration system has strong validation and clear errors.

The JSON-valid-YAML strategy is convenient and reduces dependency requirements.

The biggest long-term issue is that agent arguments are currently built using prompt string substitution.

That is fine for simple CLIs but does not scale cleanly to agents with different command protocols.

This reinforces the need for `AgentAdapter`.

---

# 17. CLI

The CLI already has a useful operational surface:

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

That is much more complete than a typical early project.

## Recommendation

Keep the CLI as a thin interface.

The long-term target should be:

```text
parse arguments
    |
    v
application service
    |
    v
render result
    |
    v
exit code
```

Move orchestration-specific logic out of `main()` as the project grows.

This is maintainability work, not an emergency bug fix.

---

# 18. Tests

The test inventory is encouraging and covers major subsystems, including:

- `test_agent_result.py`
- `test_agent_run.py`
- `test_artifacts.py`
- `test_cli.py`
- `test_config_registry.py`
- `test_events.py`
- `test_failure_kernel.py`
- `test_finalize.py`
- `test_git.py`
- `test_gui.py`
- `test_logging.py`
- `test_review_regressions.py`
- `test_runner.py`
- `test_state.py`

This indicates that the project is already testing more than just happy paths.

The presence of dedicated failure and regression tests is especially valuable for orchestration software.

---

# 19. Testing Recommendations

Even though the test architecture is good, the next stage should emphasize system invariants.

## Add more invariant-oriented tests

Examples:

### Agent run invariants

```text
RUNNING cannot transition back to PENDING
terminal states cannot become RUNNING
COMPLETED requires a recorded terminal outcome
```

### Verification invariants

```text
PASSED verification cannot contain required failed checks
cancelled verification cannot become passed
unknown/invalid check output cannot silently become success
```

### Workflow invariants

```text
completed dependency must precede dependent task
failed required verification blocks downstream success
recovery cannot create success without evidence
```

### Git invariants

```text
base changes prevent merge
unclean base worktree prevents merge
conflicted merge never becomes successful
```

### Persistence invariants

```text
reopening the database preserves execution state
migration is idempotent
recovery is idempotent
```

These tests will give the architecture a much stronger safety net.

---

# 20. Security Audit

The security foundation is good, but AgentOps should not be described as a sandbox unless it actually becomes one.

## Strong decisions already present

- subprocess execution without a shell
- restricted environment propagation
- prompt redaction
- worktree containment
- verification working-directory containment
- Git terminal prompts disabled
- explicit verification command configuration
- persistent execution evidence
- crash recovery that does not fabricate success

These are all meaningful protections.

## Important trust-boundary clarification

AgentOps is an orchestrator, not a full sandbox.

A coding agent running inside the chosen working directory can still have substantial access to the repository and local environment exposed to it.

The security model should explicitly state:

```text
AgentOps controls process launch, configuration, isolation, verification,
and persistence, but the coding agent itself is still a powerful local process.
```

If stronger containment is ever desired, that should be a distinct sandboxing project rather than being implied by the existing process runner.

---

# 21. Formal Success Model

This is one of the most important recommendations.

AgentOps already conceptually distinguishes multiple kinds of success.

Formalize them.

A useful model is:

```text
Process success
        |
        v
Agent result accepted
        |
        v
Required verification passed
        |
        v
Review approved
        |
        v
Merge eligible
        |
        v
Merge succeeded
```

Do not collapse these into one boolean.

### Suggested conceptual state graph

```text
Task
 |
 +-- AgentRun(s)
 |
 +-- VerificationRun(s)
 |
 +-- ReviewRun(s)
 |
 +-- Failure(s)
 |
 +-- RepairCycle(s)
 |
 +-- MergeRun(s)
```

The final workflow result should derive from the complete evidence graph.

---

# 22. Recommended 0.2 Architecture

The project does not need a rewrite.

A good future structure would look approximately like:

```text
agentops/
|
+-- domain/
|   +-- workflow.py
|   +-- task.py
|   +-- run.py
|   +-- verification.py
|   +-- failure.py
|   +-- merge.py
|
+-- agents/
|   +-- adapter.py
|   +-- registry.py
|   +-- pi.py
|   +-- codex.py
|   +-- opencode.py
|   +-- antigravity.py
|
+-- runtime/
|   +-- process.py
|   +-- cancellation.py
|   +-- environment.py
|
+-- orchestration/
|   +-- planner.py
|   +-- scheduler.py
|   +-- executor.py
|   +-- repair.py
|   +-- recovery.py
|
+-- verification/
|   +-- kernel.py
|   +-- profiles.py
|   +-- checks.py
|
+-- git/
|   +-- worktrees.py
|   +-- merge.py
|
+-- persistence/
|   +-- database.py
|   +-- runs.py
|   +-- tasks.py
|   +-- failures.py
|   +-- events.py
|   +-- artifacts.py
|
+-- interfaces/
    +-- cli.py
    +-- gui.py
```

This is a directional architecture, not a demand to immediately reorganize the repository.

---

# 23. Prioritized Roadmap

## P0: Stabilize Execution Semantics

Define and document exactly what each success state means.

Create explicit rules for:

```text
process success
agent success
verification success
review approval
merge eligibility
workflow success
```

Make invalid state transitions impossible or explicitly rejected.

---

## P1: Introduce AgentAdapter

Create a formal adapter boundary for coding-agent CLIs.

Minimum responsibilities:

```text
identify/detect agent
build command
build environment
expose capabilities
parse output
report supported roles
```

This should become the primary integration point for Pi, Codex, OpenCode, Antigravity, and future agents.

---

## P1: Extract ProcessRuntime

Move common process behavior out of `AgentRunner`.

Both agent execution and verification should depend on the shared runtime.

---

## P1: Decompose WorkflowEngine

Split planning, scheduling, execution, repair, and recovery into smaller components.

Keep a facade if desired.

---

## P1: Make Review and Merge First-Class

Introduce explicit domain models for:

```text
ReviewRun
MergeRun
```

This will make the final workflow state much more explainable.

---

## P2: Structured Failure Evidence

Reduce reliance on free-form string matching.

Capture structured evidence at the source, then use text heuristics only as fallback.

---

## P2: Decompose StateStore

Move domain-specific persistence into repositories while keeping a stable facade if convenient.

---

## P2: Strengthen Invariant Tests

Add state-transition, recovery, concurrency, verification, and Git safety invariants.

---

## P3: Expand Agent Integrations

Only after the abstraction stabilizes, add more adapters.

Avoid building many one-off integrations against the old command-substitution interface.

---

# 24. What Not to Do Yet

Do not prioritize these before the core execution contracts are stable:

- dozens of new agent integrations
- a giant plugin architecture
- distributed execution
- remote workers
- cloud orchestration
- a much larger GUI
- another large AI planning layer
- speculative enterprise features

The project is already technically interesting without them.

The next gains should come from reliability and architectural clarity.

---

# 25. Suggested Acceptance Criteria for the Next Major Refactor

The next major internal milestone should be considered successful when:

### Agent integration

- Every supported coding agent goes through `AgentAdapter`.
- The workflow engine does not construct agent-specific command lines directly.
- Capabilities are queryable.

### Process execution

- Agent execution and verification share `ProcessRuntime`.
- Timeout and cancellation behavior is consistent.
- Process-group cleanup is covered by tests.

### Workflow semantics

- Workflow success has one authoritative definition.
- Review and merge are explicit states.
- Recovery never fabricates success.
- Retry and repair are clearly distinguishable.

### Persistence

- Database migration remains backward-compatible.
- Recovery is idempotent.
- Execution history reconstructs correctly after restart.

### Git

- The exact base commit is recorded.
- The exact agent commit is recorded when applicable.
- Verification can be tied directly to the change being merged.
- Merge outcome is persisted.

### Testing

- State-transition invariants are covered.
- Concurrency behavior is covered.
- Cancellation is covered.
- Recovery is covered.
- Merge conflict behavior is covered.

---

# 26. Practical Design Principles to Keep

These are worth preserving as explicit project principles.

## Principle 1: Agent success is not workflow success

An agent saying “done” is not evidence that the change is correct.

## Principle 2: Verification is evidence

Tests, lint, type checking, builds, and other configured checks should be treated as evidence-producing operations.

## Principle 3: Never fabricate success during recovery

Interrupted work must remain failed, cancelled, or otherwise unresolved until fresh evidence exists.

## Principle 4: Git is the source of truth for changes

When possible, the Git graph should define what was actually changed and merged.

## Principle 5: The orchestrator is the conductor

AgentOps should coordinate coding agents, not become another giant coding agent itself.

## Principle 6: Keep agent-specific knowledge behind adapters

The orchestration core should not care how an individual CLI works.

## Principle 7: Prefer structured evidence over string heuristics

Strings are useful fallback signals, not ideal primary state.

## Principle 8: Persistence is part of correctness

A workflow that works only while the process stays alive is not a reliable orchestrator.

---

# 27. Final Bottom Line

AgentOps is no longer just a collection of scripts that launches AI coding CLIs.

It already resembles a local CI/orchestration platform for coding agents.

Its strongest qualities are:

1. Explicit execution lifecycle modeling
2. Structured results and evidence
3. Verification as a first-class subsystem
4. Deterministic failure handling
5. Retry and repair concepts
6. Crash recovery
7. Git worktree isolation
8. Persistent operational history
9. Good test coverage across major subsystems
10. Thoughtful security boundaries around subprocess execution

The project does **not** need a rewrite.

The main job now is to strengthen the contracts between the pieces that already exist.

The most important improvements are:

1. **AgentAdapter + capability model**
2. **Shared ProcessRuntime**
3. **Formal execution/result state machine**
4. **WorkflowEngine decomposition**
5. **First-class ReviewRun and MergeRun**
6. **Structured failure evidence**
7. **StateStore decomposition**
8. **More integrations only after the above stabilize**

The central architectural direction should be:

```text
               +-------------------+
               |    Workflow       |
               +---------+---------+
                         |
                         v
               +-------------------+
               |    Scheduler      |
               +---------+---------+
                         |
          +--------------+--------------+
          |                             |
          v                             v
   +-------------+               +-------------+
   | AgentAdapter|               | Verification|
   +------+------+               +------+------+
          |                             |
          +-------------+---------------+
                        |
                        v
                +---------------+
                | ProcessRuntime |
                +-------+-------+
                        |
                        v
                 external CLIs

                        |
                        v
              +-------------------+
              | Structured Evidence|
              +---------+---------+
                        |
                        v
              +-------------------+
              | Failure / Repair  |
              +---------+---------+
                        |
                        v
              +-------------------+
              | Review / Merge    |
              +-------------------+
```

The most important strategic decision is this:

> **Do not keep adding features faster than the core execution model can absorb them.**

The project has reached the point where architectural clarity will compound.

A smaller number of strong abstractions will make every future feature easier:

- adding a new agent
- changing the CLI protocol
- introducing new verification types
- building better GUI views
- adding remote execution later
- implementing richer recovery
- measuring agent reliability
- tracing workflows
- reviewing changes safely

The project is in a good position. The right next move is **evolution, not rewrite**.

---

# 28. Recommended Immediate Order of Work

For the next development cycle, the recommended order is:

```text
1. Define formal success/state semantics
2. Extract ProcessRuntime
3. Introduce AgentAdapter and capabilities
4. Refactor WorkflowEngine around the new contracts
5. Add first-class ReviewRun/MergeRun
6. Improve structured failure evidence
7. Add invariant and recovery tests
8. Decompose StateStore if growth justifies it
9. Add/expand agent adapters
10. Resume feature expansion
```

This order minimizes rework.

---

# 29. Audit Conclusion

**AgentOps is architecturally promising and already has a strong foundation.**

The codebase's biggest risk is not that it is poorly designed. The risk is that good subsystems continue growing independently until their boundaries become difficult to maintain.

Stabilize the contracts now.

Keep the execution model explicit.

Keep verification authoritative.

Keep recovery conservative.

Keep Git isolation strong.

Put agent-specific behavior behind adapters.

Then build upward.

That is the clearest path from the current 0.1.x architecture to a robust AgentOps 0.2+ platform.

---

# 27. Fresh Current-Repository Top 10 Improvements

This section is a fresh review of the current repository after the earlier audit. It should be treated as the current prioritized action list, superseding any older ordering where they differ.

## 1. Add a first-class AgentAdapter and capability model

### Problem

Agent configuration is still centered on executable names, arguments, roles, models, and timeouts. That works for launching CLIs, but it does not provide a strong abstraction for materially different coding-agent interfaces.

### Recommendation

Create an `AgentAdapter` contract responsible for:

- detection
- capabilities
- command construction
- environment construction
- prompt/input transport
- output parsing
- cancellation behavior
- role/capability support

Represent capabilities explicitly, for example:

```text
coding
planning
review
structured_output
streaming
mcp
model_selection
read_only
non_interactive
```

Keep the registry responsible for selection, but make adapters responsible for agent-specific behavior.

### Why it matters

This is the architectural foundation for cleanly supporting Pi, Codex, OpenCode, Antigravity, and future agents without teaching `WorkflowEngine` the quirks of each CLI.

### Priority

**P0/P1**

---

## 2. Extract a shared ProcessRuntime

### Problem

Agent execution and verification both need the same low-level process functionality, including environment policy, process groups, timeouts, cancellation, termination, and output collection.

The current design creates coupling between the verification and agent-running subsystems.

### Recommendation

Create a shared runtime layer:

```text
ProcessRuntime
    spawn()
    communicate()
    cancel()
    terminate()
    enforce_timeout()
    build_environment()
```

Then:

```text
ProcessRuntime
   +--> AgentRunner
   +--> VerificationKernel
```

Neither subsystem should depend on the other's private implementation.

### Priority

**P1**

---

## 3. Formalize the complete execution state machine

### Problem

Agent success, task success, verification success, review approval, workflow success, and merge success are already conceptually distinct, but the rules should become an explicit domain contract.

### Recommendation

Define clear invariants such as:

```text
Agent process exit 0
    != verified

Verified
    != reviewed

Reviewed
    != merged

Merged
    != workflow successful unless all required tasks succeeded
```

Make invalid transitions impossible or explicitly rejected.

### Target model

```text
Workflow
  -> Tasks
      -> AgentRuns
      -> VerificationRuns
      -> ReviewRuns
      -> Failure/Repair cycles
  -> MergeRun
```

### Priority

**P1**

---

## 4. Decompose WorkflowEngine before adding major features

### Problem

`WorkflowEngine` now coordinates planning, scheduling, agent execution, verification, retries, repair, recovery, persistence, and cancellation.

It is becoming a god object.

### Recommendation

Move toward:

```text
WorkflowPlanner
WorkflowScheduler
TaskExecutor
RepairCoordinator
RecoveryCoordinator
```

Keep a small `WorkflowEngine` facade if that preserves the public API.

### Priority

**P1**

Do this before adding a large number of new orchestration features.

---

## 5. Make review and merge first-class persisted operations

### Problem

Agent runs and verification runs already have strong lifecycle models. Review and Git merge operations are less formally represented.

### Recommendation

Introduce concepts such as:

```text
ReviewRun
MergeRun
```

with explicit lifecycle states and persisted evidence.

A `MergeRun` should record at least:

- base commit
- source branch/commit
- verification run IDs
- review decision
- merge result
- conflict/failure reason
- resulting commit

### Important current-code issue

The finalization layer should distinguish different `GitError` causes. A dirty base worktree, changed base commit, and an actual merge conflict are not necessarily the same recovery situation. Do not classify every Git failure as a merge conflict.

### Priority

**P1/P2**

---

## 6. Replace string-first failure classification with structured evidence

### Problem

Failure classification still depends heavily on textual clues such as command names and error-message fragments.

### Recommendation

Make structured evidence the primary input:

```text
FailureEvidence
    source
    check_class
    exit_code
    timed_out
    cancelled
    terminated
    command
    agent
    stderr
    stdout
```

Then use textual matching only when structured evidence cannot determine the category.

### Benefits

- fewer false classifications
- more deterministic repair decisions
- easier testing
- better analytics
- easier support for new agents

### Priority

**P2**

---

## 7. Make persistence failures observable and intentional

### Problem

The current code contains defensive persistence fallbacks that can preserve the main operation when state recording fails. This is useful for resilience, but silent degradation can leave the operator with incomplete history.

### Recommendation

Separate:

```text
operation success
persistence success
```

If an operation succeeds but persistence fails, surface an explicit degraded-state warning and record whatever recovery information can safely be retained.

For critical transitions, consider making persistence failure fatal when continuing would create an unsafe or ambiguous state.

### Priority

**P1/P2**

---

## 8. Strengthen artifact lifecycle and orphan recovery

### Problem

Artifacts and event persistence have been added, but artifact handling should be treated as a complete lifecycle rather than just file registration.

### Recommendation

Define explicit artifact states and ownership:

```text
created
attached
verified
preserved
cleaned
orphaned
```

Add recovery logic for artifacts left behind by crashes or interrupted runs.

Every artifact should have enough metadata to answer:

```text
which workflow?
which task?
which run?
which worktree?
what created it?
when?
can it be safely deleted?
```

### Priority

**P2**

---

## 9. Add continuous CI/regression gating

### Problem

The repository has a substantial test suite, but orchestration software needs continuous regression protection because small changes to lifecycle code can cause subtle state-machine regressions.

### Recommendation

Make CI run at minimum:

```text
unit tests
integration tests
lint
type checking
package/build validation
```

Add targeted regression tests for:

- duplicate task claims
- cancellation during agent execution
- cancellation during verification
- timeout recovery
- repeated recovery idempotency
- merge/base-commit races
- missing-agent fallback
- malformed agent output
- persistence failures
- artifact orphan recovery

### Priority

**P1**

---

## 10. Split StateStore behind a stable facade

### Problem

`StateStore` is becoming responsible for nearly every persistence concern.

### Recommendation

Gradually introduce repositories:

```text
WorkflowRepository
TaskRepository
RunRepository
VerificationRepository
FailureRepository
EventRepository
ArtifactRepository
```

Keep `StateStore` as a compatibility facade during migration.

### Benefits

- smaller modules
- easier tests
- clearer transactions
- easier future storage migration
- less coupling between domain and SQL

### Priority

**P2**

---

# 28. Recommended Implementation Order

Do not implement the ten recommendations randomly.

Use this order:

```text
Phase A: Stabilize contracts

1. Formal execution state machine
2. AgentAdapter + capability model
3. Shared ProcessRuntime
4. Structured failure evidence

Phase B: Simplify orchestration

5. Decompose WorkflowEngine
6. First-class ReviewRun/MergeRun
7. Fix Git failure classification

Phase C: Strengthen durability

8. Persistence failure policy
9. Artifact lifecycle/recovery
10. StateStore repository split

Phase D: Regression protection

11. CI gating
12. Expand targeted integration/regression tests
```

The exact implementation order can change if a prerequisite becomes blocking, but the core principle should remain: **stabilize the execution model before expanding the feature surface.**

---

# 29. What Not to Do Yet

Do not respond to these findings by rewriting AgentOps from scratch.

Do not immediately add:

- dozens of new agent integrations
- distributed workers
- remote execution
- cloud orchestration
- a huge plugin framework
- a second AI planning layer
- large GUI feature expansions

The current system already contains too much valuable domain logic to justify a rewrite.

The correct strategy is targeted refactoring around the orchestration kernel.

---

# 30. Current Architectural Target

The long-term architecture should converge toward:

```text
                         +----------------+
                         |   CLI / GUI    |
                         +-------+--------+
                                 |
                                 v
                         +----------------+
                         | WorkflowEngine |
                         |    Facade      |
                         +-------+--------+
                                 |
              +------------------+------------------+
              |                  |                  |
              v                  v                  v
        WorkflowPlanner   WorkflowScheduler   RecoveryCoordinator
                                 |
                                 v
                           TaskExecutor
                                 |
                    +------------+------------+
                    |                         |
                    v                         v
              AgentAdapter             VerificationKernel
                    |                         |
                    +------------+------------+
                                 |
                                 v
                          ProcessRuntime
                                 |
                                 v
                            Subprocesses

          +------------------- Persistence -------------------+
          |                                                    |
          v                                                    v
   Domain repositories                                  Event/Artifact
          |                                                    |
          +----------------------+-----------------------------+
                                 |
                                 v
                               SQLite

                     +----------------------+
                     | Git / Worktree layer |
                     | ReviewRun / MergeRun  |
                     +----------------------+
```

This preserves the project's existing strengths while reducing coupling.

---

# 31. Full Bottom Line

AgentOps is already a serious local coding-agent orchestration project, not merely a wrapper around subprocess calls.

Its strongest existing ideas are:

1. Explicit persistent agent lifecycle.
2. Prompt privacy.
3. Run lineage for retries and repairs.
4. Structured agent results.
5. Verification as an independent concept.
6. Deterministic failure categories and recovery policies.
7. Crash recovery that does not invent success.
8. Git worktree isolation.
9. Dependency-aware scheduling.
10. Persistent events and artifacts.
11. Defensive process management.
12. A broad regression-oriented test suite.

The project does not need a rewrite.

It needs a stronger center.

That center should be an explicit orchestration contract connecting:

```text
Workflow
  -> Task
      -> AgentRun
      -> VerificationRun
      -> ReviewRun
      -> Repair/Failure cycles
  -> MergeRun
```

The single most important architectural improvement is the **AgentAdapter + capability model**. It separates the orchestration system from the quirks of individual coding-agent CLIs.

The second most important improvement is **ProcessRuntime**, which gives agents and verification a shared, clean process boundary.

The third is making **success semantics explicit** so AgentOps never confuses an agent's claim with actual verified project state.

After that, reduce the size of the two main god objects: `WorkflowEngine` and `StateStore`.

Finally, make review, merge, persistence degradation, artifacts, and structured failure evidence first-class parts of the system.

## Final recommendation

**Do not build more features first. Stabilize the kernel first.**

Once these contracts are solid, adding Pi, Codex, OpenCode, Antigravity, future agents, richer GUI features, and more sophisticated workflows becomes substantially easier and safer.

AgentOps should be the **conductor**, not another coding agent.

Its job is to decide:

```text
who should act
what they should do
where they should work
what evidence is required
whether the result is actually acceptable
what to do when it fails
and whether the resulting change can be safely merged
```

That is the clearest path from the current project to a robust personal multi-agent development platform.

---

# 32. Audit Status

This document was updated from a fresh review of the current repository after the earlier AgentOps audit.

Important current observations include:

- the repository has continued adding events and artifacts
- SQLite initialization has received race-safety improvements
- worktree finalization has been centralized
- recovery and failure idempotency have improved
- the repository reports a substantially expanded passing test suite
- the remaining highest-value work is increasingly architectural rather than feature-count driven

No source-code changes were made as part of this recommendation update.

The intended next step is to turn the prioritized recommendations into small, testable implementation plans rather than attempting a large rewrite.
