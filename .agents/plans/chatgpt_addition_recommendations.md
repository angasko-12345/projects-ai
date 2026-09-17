# AgentOps Addition Recommendations

## Current revision

**Revised:** 2026-09-17  
**Repository:** `angasko-12345/projects-ai`  
**Purpose:** Product and UX recommendations that build on the current AgentOps kernel without creating a second orchestration system.

> This document is the current product plan. Backend capabilities that are already implemented are explicitly marked as such. Features that are not implemented remain in the plan.

---

# Product Goal

AgentOps should provide a simple:

```text
Task → Done
```

experience.

A user should be able to give AgentOps a high-level software task without needing to understand:

- workflows
- agent adapters
- worktrees
- verification kernels
- failure categories
- retries
- repair runs
- Git branches
- persistence
- task dependencies
- agent routing

For example:

```text
Add dark mode to the settings page.
```

AgentOps should internally handle:

```text
Understand
   ↓
Plan
   ↓
Choose agents
   ↓
Create isolated worktree
   ↓
Implement
   ↓
Verify
   ↓
Repair if necessary
   ↓
Review
   ↓
Ask human if necessary
   ↓
Merge
   ↓
Report
```

The user should primarily see decisions, progress, evidence, changes, and outcomes.

---

# Current Backend Foundations

The product layer can now build on several verified foundations:

- AgentAdapter + capability model
- shared ProcessRuntime
- formal execution state model
- structured agent results
- structured failure evidence
- capability-aware deterministic routing
- persisted event infrastructure
- artifact registry and orphan helpers
- persisted worktree provenance
- controller/service boundary for the GUI
- verification profiles and verification runs
- retry/repair lineage
- Git worktree isolation

The product layer must consume these foundations rather than recreate them.

---

# Product Architecture

The intended layering is:

```text
AgentOps UI
    |
    v
Application / Controller Services
    |
    +-- Task Service
    +-- Workflow Service
    +-- Approval Service
    +-- Recovery Service
    +-- Project Service
    |
    v
Existing AgentOps Kernel
    |
    +-- WorkflowEngine
    +-- AgentAdapter
    +-- ProcessRuntime
    +-- Verification
    +-- Failure / Recovery
    +-- Routing
    +-- Git / Worktrees
    +-- Events
    +-- Artifacts
    +-- StateStore
```

The UI should not become a second source of truth.

The controller/application layer should remain the boundary between presentation and orchestration.

---

# 1. New Task Screen

**Status:** Open  
**Priority:** P1

## Goal

Provide a simple interface for starting a task without requiring users to understand AgentOps internals.

Example:

```text
┌──────────────────────────────────────────────┐
│ New Task                                     │
│                                              │
│ What should AgentOps do?                     │
│ ┌──────────────────────────────────────────┐ │
│ │ Add dark mode to the settings page       │ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ Project       [ projects-ai           ▾ ]    │
│ Agent         [ Auto-select           ▾ ]    │
│ Verification  [ Standard             ▾ ]    │
│                                              │
│              [ Start Task ]                  │
└──────────────────────────────────────────────┘
```

## Requirements

Accept natural-language task descriptions.

Allow project selection.

Use Auto agent selection by default.

Provide verification presets.

Provide an advanced configuration option for power users.

Create the appropriate internal workflow automatically.

Do not expose orchestration complexity by default.

## Important implementation rule

The existing workflow creation and dependency model remains authoritative.

The New Task screen should compile the user request into existing tasks and workflow objects. It must not create a second workflow representation.

---

# 2. Mission Control Dashboard

**Status:** Open  
**Priority:** P1

## Goal

Make the main screen immediately useful.

The dashboard should answer:

1. What is running?
2. What finished?
3. What failed?
4. What needs human attention?
5. What changed?

Example:

```text
AGENTOPS

3 Running     12 Completed     1 Failed     1 Needs You
```

Then show:

```text
RUNNING

● Add authentication
  OpenCode → implementing
  4m 21s

● Refactor database layer
  Pi → testing
  8m 02s
```

and:

```text
NEEDS ATTENTION

⚠ Review required
  Add payment validation
```

The dashboard should feel like mission control, not a database administration interface.

---

# 3. Live Agent Activity Feed

**Status:** Open  
**Priority:** P1

## Goal

Allow users to understand what AgentOps is doing while agents are running.

Example:

```text
14:32:08  OpenCode
          Started task

14:32:14  OpenCode
          Reading src/auth/

14:32:31  OpenCode
          Modified auth.py

14:32:47  Verification
          Running pytest

14:33:02  Verification
          42 passed

14:33:08  Reviewer
          Reviewing changes

14:33:21  AgentOps
          ✓ Task completed
```

## Requirements

Show high-level observable events:

- task started
- agent selected
- worktree created
- file changed
- command executed
- verification started
- verification completed
- failure detected
- repair started
- review started
- approval requested
- merge completed

Do not expose private chain-of-thought.

The feed should expose actions, tools, files, commands, verification, and outcomes.

## Current implementation opportunity

The existing event infrastructure should become the feed source instead of adding a new event system.

The current GUI can continue to poll initially, but a future subscription-based event path should remove unnecessary polling once the interface is ready.

---

# 4. Explain This Run

**Status:** Open  
**Priority:** P1

## Goal

Provide a concise human-readable summary of every completed or failed run.

Example:

```text
RUN #184

Goal
Add dark mode.

What happened
• OpenCode modified 7 files
• 14 tests added
• 126 tests passed
• Reviewer found 1 issue
• Agent repaired the issue
• Final verification passed

Result
✓ Successfully completed

Time
6m 42s

Attempts
2

Files changed
7
```

## Requirements

Summarize:

- original goal
- agents used
- major workflow stages
- files changed
- verification results
- failures
- repairs
- reviews
- final status
- duration
- attempts

Default to concise summaries.

Allow detailed inspection for power users.

Do not synthesize hidden reasoning.

---

# 5. Visual Workflow Builder

**Status:** Future  
**Priority:** P3

## Goal

Eventually provide a visual workflow editor.

Example:

```text
Task
  ↓
Planner
  ↓
Implementation
  ↓
Tests
  ↓
Reviewer
  ↓
Approval
  ↓
Merge
```

## Important

Do not make this the primary interface.

Power users will benefit from it.

Beginners should never need it.

The visual editor must be another representation of the same underlying workflow DAG and task model.

It must not become a second workflow engine.

---

# 6. Agent Profiles

**Status:** Backend partial, UI open  
**Priority:** P2

## Goal

Make agent roles understandable to users.

Profiles could include:

```text
Implementer
Writes code and tests.

Reviewer
Reviews changes and identifies problems.

Debugger
Investigates failing tests and repairs implementations.

Planner
Breaks large tasks into smaller tasks.

Explorer
Investigates a repository without modifying it.
```

## Backend mapping

Profiles should map to:

- AgentAdapter
- capabilities
- allowed operations
- model configuration
- verification requirements
- role compatibility

The existing capability-aware routing infrastructure already provides the backend foundation.

The remaining work is making the profiles visible and understandable without exposing internal implementation details.

---

# 7. Automatic Agent Selection

**Status:** Backend implemented, UI/product exposure open  
**Priority:** P1

## Goal

Allow:

```text
Agent: Auto
```

AgentOps should determine the appropriate agent.

Conceptually:

```text
Task
 ↓
Required capabilities
 ↓
Available agents
 ↓
Compatible agents
 ↓
Routing decision
```

The current router already produces explainable decisions.

The UI should present them simply:

```text
Selected: OpenCode

Why:
Best available implementation fit

Also considered:
Pi
Codex
```

## Selection factors

Use:

- capabilities
- role
- language/tool support
- project requirements
- agent availability
- configured priority
- historical performance
- user preferences
- health signals when available

The user must be able to override automatic selection.

Do not turn the router's internal score into a flashy pseudo-confidence metric.

---

# 8. Human Approval Gates

**Status:** Kernel/UI open  
**Priority:** P1

## Goal

Provide controlled autonomy.

Example:

```text
⚠ APPROVAL REQUIRED

Agent wants to:
Merge 14 changed files into main.

Changes:
+382
-147

Tests:
✓ 183 passed

Review:
✓ Approved

[ View Diff ]
[ Approve & Merge ]
[ Reject ]
[ Send Back ]
```

## Policies

Support:

- Never ask me
- Ask for important actions
- Ask before merge
- Ask before destructive operations

## Approval actions

At minimum:

- approve
- reject
- send back for repair
- inspect diff
- inspect verification
- inspect logs

Approval must become a first-class persisted operation.

The UI must never treat a temporary button click as the authoritative approval record.

---

# 9. Diff Viewer

**Status:** Open  
**Priority:** P1

## Goal

Allow users to inspect changes without leaving AgentOps.

Example:

```text
RUN #184

Files changed: 7

✓ auth.py
✓ login.py
✓ settings.py
⚠ tests/test_auth.py
```

Then show a normal diff.

## Actions

Provide:

- open in editor
- view file
- view commit
- ask agent to explain
- inspect verification
- inspect review
- approve/reject where appropriate

Do not make "Accept" mean silently applying a second set of changes.

The authoritative code state remains the Git worktree/commit.

---

# 10. Fix It Actions

**Status:** Backend recovery exists, UI action open  
**Priority:** P1

## Goal

Make recovery simple.

Example:

```text
✗ Tests failed

3 failures

[ View Failures ]
[ Ask Agent to Fix ]
[ Retry ]
[ Stop ]
```

### Important semantic distinction

```text
Retry
= repeat execution with essentially the same task/context

Repair
= intentionally modify the implementation or execution context before another attempt
```

"Ask Agent to Fix" should create a proper repair lineage.

Do not collapse retry and repair into a single operation.

---

# 11. Run Timeline

**Status:** Events backend exists, UI open  
**Priority:** P1

## Goal

Give every workflow a visual history.

Example:

```text
START
 │
 ├── Planning        ✓ 12s
 ├── Implementation  ✓ 2m 41s
 ├── Tests            ✗ 18s
 ├── Repair           ✓ 1m 02s
 ├── Tests            ✓ 21s
 ├── Review           ✓ 17s
 └── Merge            ✓ 4s
```

Selecting a stage should expose:

- logs
- files
- artifacts
- verification
- agent
- result
- failure evidence
- duration
- related events

This becomes the flight recorder for coding-agent execution.

---

# 12. Run Replay

**Status:** Future  
**Priority:** P3

## Goal

Allow users to replay the observable history of a run.

Example:

```text
RUN #184

◀   ▶

14:32:08 Started
14:32:14 Agent selected
14:32:20 Worktree created
14:32:31 File changed
14:32:47 Tests started
14:33:02 Tests failed
14:33:08 Repair started
...
```

Use persisted events as the source.

Replay observable workflow history.

Do not attempt to reproduce hidden model reasoning.

---

# 13. "What Changed?"

**Status:** Open  
**Priority:** P2

## Goal

Provide a project-level summary since the user last opened AgentOps.

Example:

```text
What changed?

3 tasks completed
1 task failed
17 files changed
246 tests run
238 passed
8 failed → repaired
```

Also show:

- agents used
- recent workflows
- recent commits
- recent failures
- pending approvals

This should live on the project dashboard.

---

# 14. Project Overview

**Status:** Partial concepts exist, product surface open  
**Priority:** P2

Each repository should have its own dashboard.

Example:

```text
projects-ai

Health        ✓ Healthy
Branch        main

TASKS
12 completed
2 running
1 failed

AGENTS
OpenCode      8 runs
Pi            5 runs

VERIFICATION
246 total
238 passed
8 failed

RECENT CHANGES
...
```

Include:

- repository status
- current branch
- active workflows
- recent tasks
- agent activity
- verification health
- recent commits
- recent failures
- pending approvals
- active worktrees

Do not make "health" a single opaque score.

Show the evidence behind it.

---

# 15. Agent Health Page

**Status:** Detection backend exists, health UI open  
**Priority:** P2

AgentOps should understand installed-agent availability.

Example:

```text
AGENTS

✓ OpenCode
  Available

✓ Pi
  Available

⚠ Codex
  Available
  Recent failures

✗ Antigravity
  Not detected
```

For unavailable agents:

```text
Why isn't this agent available?

Executable not found.

[ Configure ]
[ Rescan ]
```

Do not force users to interpret raw subprocess or PATH errors.

Historical success metrics should be shown as context, not as a simplistic quality score.

---

# 16. Automatic Setup Wizard

**Status:** Open  
**Priority:** P2

## Goal

Make first launch easy.

Example:

```text
Welcome to AgentOps

✓ Python detected
✓ Git detected

Agents found:

✓ OpenCode
✓ Pi
✓ Codex
✓ Antigravity

[ Continue ]
```

Then:

```text
Choose defaults

Implementation: [ Auto ]
Review:         [ Auto ]
Verification:  [ Standard ]

[ Finish Setup ]
```

Zero-config should be the normal path.

Advanced configuration should remain available.

---

# 17. Project Onboarding

**Status:** Open  
**Priority:** P2

Provide:

```text
agentops init
```

or equivalent GUI onboarding.

Detect common repository characteristics:

```text
Python project
Git repository
pytest detected
ruff detected
mypy detected
```

Then recommend a verification profile:

```text
Python Standard

✓ pytest
✓ ruff
○ mypy

[ Accept ]
[ Customize ]
```

Do not force automatic modification of project configuration without explicit user approval.

---

# 18. Smart Notifications

**Status:** Open  
**Priority:** P2

Do not notify users about every internal event.

Primary notifications:

```text
✓ Task completed
✗ Task failed
⚠ Approval required
⚠ Agent unavailable
⚠ Verification failed
```

Optional:

```text
AgentOps is still working...
```

Desktop notifications can come later.

Notifications should be configurable and deduplicated.

---

# 19. Cost and Resource Dashboard

**Status:** Open  
**Priority:** P3

AgentOps may use both local and cloud models.

Example:

```text
TODAY

Runs             17
Agent time       42m
LLM requests     83
Estimated cost   $0.82
```

Track where available:

- execution time
- model requests
- token usage
- estimated cost
- local vs remote execution
- provider/model identity

Local models should naturally show $0 when there is no metered cost.

Do not invent cost values when the provider does not expose enough information.

---

# 20. Budget and Safety Controls

**Status:** Partial backend controls, product expansion open  
**Priority:** P2

AgentOps should protect users from runaway workflows.

Example:

```text
Maximum runtime: 30 min
Maximum retries: 3
Maximum tasks: 20
Approval before merge: ✓
```

Potential controls:

- maximum concurrent agents
- maximum workflow depth
- maximum repair attempts
- maximum verification time
- maximum total runtime
- maximum estimated cloud cost

The enforcement belongs in the orchestration kernel/configuration layer, not only the GUI.

---

# 21. "Why Did It Fail?"

**Status:** Structured failure backend exists, UX open  
**Priority:** P1

Failure information should be understandable.

Instead of:

```text
FAILURE
PROCESS_ERROR
exit code 1
```

show:

```text
WHY THIS FAILED

The implementation agent completed.

Verification failed because:

tests/test_auth.py::test_expired_token

expected 401
received 200

AgentOps classified this as:
TEST_FAILURE

Recommended action:
Repair implementation
```

Actions:

```text
[ Fix Automatically ]
[ View Test ]
[ Retry ]
[ Stop ]
```

The UI should consume the structured failure evidence already produced by the kernel.

Do not parse arbitrary stderr in the GUI.

---

# 22. Recovery Center

**Status:** Backend recovery exists, UI open  
**Priority:** P2

Provide a central view of unresolved problems.

Example:

```text
RECOVERY CENTER

3 unresolved problems

1. Verification failed
   Recommended: Repair

2. Base branch changed
   Recommended: Inspect and retry merge

3. Agent unavailable
   Recommended: Choose another agent
```

Each entry should provide safe next actions.

Do not hide unresolved work inside old workflow history.

---

# 23. Global Search

**Status:** Open  
**Priority:** P3

Search:

- tasks
- workflows
- runs
- failures
- files
- commits
- artifacts
- agents
- events
- logs

Eventually support natural-language search.

Start with indexed structured metadata.

Do not make raw log text the primary search database.

---

# 24. Command Palette

**Status:** Open  
**Priority:** P3

Provide a command palette such as:

```text
Ctrl+K
```

Commands:

```text
New Task
Open Project
Open Latest Run
Show Failures
Show Approvals
Run Doctor
Refresh Agents
View Worktrees
Open Settings
```

Power users should be able to navigate without digging through tabs.

---

# 25. AgentOps Doctor

**Status:** Open  
**Priority:** P2

Provide:

```text
agentops doctor
```

and a GUI equivalent.

Check:

- Python
- Git
- SQLite
- migrations
- agent registry
- agent executables
- worktree directories
- verification configuration
- permissions
- runtime environment

Example:

```text
AgentOps Doctor

✓ Python
✓ Git
✓ SQLite
✓ State database
✓ OpenCode
✓ Pi

⚠ Codex

Executable was not found.

Suggested action:
Configure the Codex executable path.

[ Fix ]
```

Doctor should be read-only by default. Repairs should require explicit confirmation.

---

# 26. Task Templates

**Status:** Open  
**Priority:** P2

Provide templates:

```text
Bug Fix
Feature
Refactor
Tests
Documentation
Dependency Update
Code Review
Full Project Task
```

Templates should compile into ordinary AgentOps tasks/workflows.

They must not create a second workflow engine.

---

# 27. Workflow Presets

**Status:** Verification/config foundations exist; product presets open  
**Priority:** P2

Offer simple presets:

```text
Fast
Standard
Thorough
Autonomous
```

Example:

```text
Fast
Implementation
→ Verification

Standard
Planning
→ Implementation
→ Verification
→ Review

Thorough
Planning
→ Implementation
→ Verification
→ Repair if needed
→ Review
→ Approval
→ Merge
```

Presets should compile to the same internal task graph.

Advanced users can customize them.

---

# 28. AgentOps Inbox

**Status:** Open  
**Priority:** P1

This should become the central human-attention interface.

The user should open AgentOps and see:

```text
INBOX

⚠ Merge approval required
⚠ Verification failed
⚠ Agent unavailable
⚠ Manual recovery needed
```

Each item should answer:

```text
What happened?
Why does AgentOps need me?
What are my choices?
What evidence do I have?
What happens if I choose each action?
```

Core principle:

```text
Do not make the user operate the orchestration system.

Make the system operate the orchestration system.
```

The Inbox depends on a persisted approval/attention domain.

---

# 29. Project Activity Graph

**Status:** Future  
**Priority:** P3

Visualize:

```text
Task
 ↓
Agent
 ↓
Verification
 ↓
Repair
 ↓
Review
 ↓
Approval
 ↓
Merge
```

Allow users to inspect:

- agent runs
- retry relationships
- repair relationships
- failures
- verification
- artifacts
- commits
- approvals

Important:

The graph is a visualization of persisted domain data.

It is not a separate source of truth.

---

# UX Priority Roadmap

## Phase 1: Task → Done

Build the smallest complete product loop:

1. New Task
2. Automatic agent selection
3. Mission Control
4. Live activity
5. Run timeline
6. Failure explanation
7. Diff viewer
8. Retry / Repair
9. Approval before merge
10. Final result summary

This is the most important product phase.

The goal is one excellent end-to-end flow, not twenty disconnected screens.

---

## Phase 2: Make the application easy

1. Agent Profiles
2. Project Overview
3. Agent Health
4. Project Onboarding
5. Setup Wizard
6. Recovery Center
7. Task Templates
8. Workflow Presets
9. AgentOps Doctor
10. Command Palette

---

## Phase 3: Make it powerful

1. Run Replay
2. Global Search
3. Cost/resource tracking
4. Budgets
5. Notifications
6. Project analytics
7. Activity Graph
8. Visual Workflow Builder

---

# Product Model

The user-facing mental model should become:

```text
Project
  ↓
Task
  ↓
Workflow
  ↓
Stages
  ↓
Runs
  ↓
Evidence
  ↓
Review
  ↓
Approval
  ↓
Merge
```

Underneath that, AgentOps retains its more detailed domain model:

```text
Workflow
 ├── Tasks
 │    ├── AgentRuns
 │    ├── VerificationRuns
 │    ├── Repair/Retry lineage
 │    ├── Failures
 │    └── Artifacts
 │
 ├── ReviewRuns
 ├── Approvals
 └── MergeRuns
```

---

# Implementation Rules

## 1. Build on existing events

The existing event infrastructure should power:

- activity feed
- timeline
- replay
- notifications
- audit
- future API consumers

Do not add another event store.

## 2. Build on existing artifacts

Use the current artifact registry for:

- diffs
- generated reports
- logs
- verification output
- review evidence

Do not create another attachment database.

## 3. Build on existing routing

Expose the B7 router.

Do not implement agent selection independently in the GUI.

## 4. Build on existing failure evidence

Create a human-readable explanation layer.

Do not add separate error parsing in the GUI.

## 5. Keep retry and repair separate

The UI should make that distinction visible.

## 6. Keep Git authoritative

"Accepting" a diff in the UI should not bypass the actual worktree/commit lifecycle.

## 7. Keep the controller boundary

The GUI should remain a client of application/controller services.

## 8. Keep the CLI consistent with the GUI

Important actions should be possible from both where practical.

For example:

```text
agentops task
agentops status
agentops failures
agentops recover
agentops doctor
```

The GUI should not invent semantics unavailable to the CLI.

---

# What Not to Do

Do not:

- rewrite the AgentOps kernel to build the GUI
- add a second workflow engine for the visual editor
- hardcode agent names into product logic
- duplicate SQLite logic in UI code
- expose chain-of-thought
- force users through advanced workflow configuration
- make raw logs the primary UX
- treat every failure as a generic "agent failed"
- collapse retry and repair
- make the UI the source of truth
- introduce a giant settings page before the main flow works
- add a marketplace before the adapter boundary is stable
- make the dashboard a giant collection of tables
- create a visual DAG editor before the DAG model is mature
- invent cost figures when provider data is unavailable

---

# Complete Example User Experience

User opens AgentOps.

They select:

```text
New Task
```

They type:

```text
Add dark mode to the settings page.
```

AgentOps automatically:

```text
detects project
↓
creates workflow
↓
routes implementation to an appropriate agent
↓
creates isolated worktree
↓
implements change
↓
runs configured verification
```

Suppose tests fail.

The UI shows:

```text
Tests failed

2 failures

Why:
An authentication assertion changed unexpectedly.

Recommended:
Repair implementation

[ Fix Automatically ]
[ Retry ]
[ View Failures ]
```

The user selects:

```text
Fix Automatically
```

AgentOps creates a repair run.

Verification passes.

Review completes.

The UI shows:

```text
READY TO MERGE

7 files changed
183 tests passed
1 repair performed
Review passed

[ View Diff ]
[ Approve & Merge ]
```

After approval:

```text
DONE

Dark mode implemented.

7 files changed
183 tests passed
1 repaired issue
Review passed
Merged successfully

6m 42s
```

The important point is that the user never had to manage:

- worktree paths
- task dependencies
- subprocesses
- retries
- repair cycles
- agent command flags
- SQLite state
- failure classification

AgentOps handled those concerns.

---

# Success Criteria

AgentOps feels like a real application when a normal user can:

1. Start a task in one screen.
2. Understand what is happening without reading logs.
3. See which agent was selected and why.
4. See verification results.
5. Understand failures in plain language.
6. Trigger retry or repair without manually editing workflows.
7. Inspect the exact change.
8. Approve important actions.
9. See a complete final summary.
10. Recover from interrupted/failed work without touching the filesystem manually.

Power users should still be able to inspect:

- runs
- events
- artifacts
- worktrees
- routing decisions
- verification details
- failure evidence
- commits
- advanced workflow graphs

The advanced layer should be available, not mandatory.

---

# Final Product Direction

The core promise should be:

```text
Give AgentOps a task.
↓
AgentOps decides how to execute it.
↓
AgentOps shows progress.
↓
AgentOps verifies the work.
↓
AgentOps repairs safe failures.
↓
AgentOps asks when a human decision matters.
↓
AgentOps shows exactly what changed.
↓
AgentOps merges only when the required conditions are satisfied.
↓
Done.
```

The project already has much of the difficult machinery underneath this experience.

The next major step is not adding more orchestration concepts.

It is turning the existing machinery into a product that feels simple while remaining powerful underneath.
