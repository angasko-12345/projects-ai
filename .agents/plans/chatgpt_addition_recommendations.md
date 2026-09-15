AgentOps Addition Recommendations

Purpose

This document contains additional recommendations for evolving AgentOps from a technically capable orchestration system into a user-friendly application.

The existing AgentOps architecture already provides strong foundations:

Agent execution

Workflows

Verification

Retry and repair

Failure classification

Git worktree isolation

Artifact persistence

SQLite state

CLI

GUI

Agent discovery

Run tracking


The next phase should focus heavily on the product and UX layer.

The goal is not to replace the existing architecture.

The goal is to put a simple, intuitive application experience on top of it.


---

Core Product Goal

AgentOps should eventually provide a simple:

Task → Done

experience.

A user should be able to give AgentOps a high-level software task without needing to understand:

workflows

agent adapters

worktrees

verification kernels

failure categories

retries

repair runs

Git branches

persistence

task dependencies

agent selection


For example:

Add dark mode to the settings page.

AgentOps should internally handle:

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

The user should primarily see the important decisions and outcomes.


---

1. New Task Screen

Goal

Provide a simple interface for starting a task without requiring users to understand AgentOps internals.

Example:

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

Requirements

Accept natural-language task descriptions.

Allow project selection.

Allow Auto agent selection.

Provide simple verification presets.

Provide an advanced configuration option for power users.

Create the appropriate internal workflow automatically.

Do not expose internal orchestration complexity by default.


Expected internal flow

User Task
    ↓
Task creation
    ↓
Planning
    ↓
Agent selection
    ↓
Implementation
    ↓
Verification
    ↓
Review
    ↓
Finalization


---

2. Mission Control Dashboard

Goal

Make the main screen immediately useful.

The dashboard should answer:

1. What is running?


2. What finished?


3. What failed?


4. What needs human attention?


5. What changed?



Example:

AGENTOPS

3 Running     12 Completed     1 Failed     1 Needs You

────────────────────────────────────────────

RUNNING

● Add authentication
  Codex → implementing
  ███████████░░░ 78%
  4m 21s

● Refactor database layer
  OpenCode → testing
  █████████████░ 91%
  8m 02s

────────────────────────────────────────────

NEEDS ATTENTION

⚠ Review required
  "Add payment validation"

────────────────────────────────────────────

RECENT

✓ Fix login redirect
✓ Update README
✗ API migration
✓ Add unit tests

The dashboard should feel like mission control, not a database administration interface.


---

3. Live Agent Activity Feed

Goal

Allow users to understand what AgentOps is doing while agents are running.

Example:

14:32:08  Codex
          Started task

14:32:14  Codex
          Reading src/auth/

14:32:31  Codex
          Modified auth.py

14:32:47  Codex
          Running pytest

14:33:02  Verification
          42 passed

14:33:08  Reviewer
          Reviewing changes

14:33:21  AgentOps
          ✓ Task completed

Requirements

Show high-level events such as:

task started

agent selected

worktree created

file changed

command executed

verification started

verification completed

failure detected

repair started

review started

approval requested

merge completed


Do not expose private chain-of-thought.

The feed should expose actions, tools, files, commands, verification, and outcomes, not hidden reasoning.


---

4. Explain This Run

Goal

Provide a human-readable summary of a run.

Every completed or failed run should have an Explain action.

Example:

RUN #184

Goal
Add dark mode.

What happened
• Codex modified 7 files
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

Requirements

The explanation should summarize:

original goal

agents used

major workflow stages

files changed

verification results

failures

repairs

reviews

final status

duration

number of attempts


The summary should be concise by default.

Allow a detailed view for power users.


---

5. Visual Workflow Builder

Goal

Eventually provide a visual workflow editor.

Example:

┌──────────┐
              │   Task   │
              └────┬─────┘
                   ↓
              ┌──────────┐
              │  Planner │
              └────┬─────┘
                   ↓
          ┌────────┴────────┐
          ↓                 ↓
     ┌─────────┐       ┌─────────┐
     │ Codex   │       │ OpenCode│
     └────┬────┘       └────┬────┘
          └────────┬─────────┘
                   ↓
              ┌──────────┐
              │  Tests   │
              └────┬─────┘
                   ↓
              ┌──────────┐
              │ Reviewer │
              └────┬─────┘
                   ↓
              ┌──────────┐
              │  Merge   │
              └──────────┘

Important

Do not make this the primary interface initially.

Power users will benefit from it.

Beginners should never need to use it.

The visual editor should eventually be another representation of the same underlying workflow model, not a separate workflow engine.


---

6. Agent Profiles

Goal

Make agent roles understandable to users.

Instead of exposing only:

agent = codex
role = implementation
model = ...

provide profiles such as:

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

Architecture

Internally map profiles to:

agent adapters

capabilities

allowed operations

preferred models

verification requirements

role compatibility


This should build on the planned AgentAdapter + capability model.


---

7. Automatic Agent Selection

Goal

Allow users to select:

Agent: Auto

AgentOps should determine the appropriate agent.

Conceptually:

Task
 ↓
Required capabilities
 ↓
Available agents
 ↓
Compatible agents
 ↓
Best candidate

Example:

Task requires:

✓ coding
✓ repository access
✓ terminal
✓ Python
✓ testing

Selected:
Codex

Reason:
Best available implementation agent

Requirements

Agent selection should consider:

capabilities

role

language support

tool availability

project requirements

agent health

recent success rate

user preferences

availability

configured priority


The user should be able to override automatic selection.


---

8. Human Approval Gates

Goal

Provide controlled autonomy.

AgentOps should pause when a workflow reaches an action requiring human approval.

Example:

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

User policies

Support policies such as:

Never ask me
Ask for important actions
Ask before merge
Ask before destructive operations

Approval actions

At minimum:

approve

reject

send back for repair

inspect diff

inspect verification

inspect logs


Approval should become a first-class persisted operation.


---

9. Diff Viewer

Goal

Allow users to inspect changes without leaving AgentOps.

Example:

RUN #184

Files changed: 7

✓ auth.py
✓ login.py
✓ settings.py
⚠ tests/test_auth.py

────────────────────────────

auth.py

- old code
+ new code

Actions

Provide:

Accept

Reject

Open in editor

View file

View commit

Ask agent to explain


The explanation feature should use relevant run context without exposing hidden chain-of-thought.


---

10. Fix It Actions

Goal

Make recovery simple.

Example:

✗ Tests failed

3 failures

tests/test_auth.py
tests/test_login.py
tests/test_session.py

[ View Failures ]

[ Ask Agent to Fix ]

[ Retry ]

[ Stop ]

Ask Agent to Fix should create a proper REPAIR run.

Do not collapse retry and repair into the same operation.

The existing distinction between retry and repair should remain explicit.


---

11. Run Timeline

Goal

Give every run a visual history.

Example:

START
 │
 ├── Planning       ✓ 12s
 │
 ├── Implementation ✓ 2m 41s
 │
 ├── Tests          ✗ 18s
 │
 ├── Repair         ✓ 1m 02s
 │
 ├── Tests          ✓ 21s
 │
 ├── Review         ✓ 17s
 │
 └── Merge          ✓ 4s

Each stage should be selectable.

Selecting a stage should expose:

logs

files

artifacts

verification

agent

result

failure information

duration


This becomes the flight recorder for coding-agent execution.


---

12. Run Replay

Goal

Allow users to replay the history of a run.

Example:

RUN #184

◀  | ▶

14:32:08  Started
14:32:14  Agent selected
14:32:20  Worktree created
14:32:31  File changed
14:32:47  Test started
14:33:02  Test failed
14:33:08  Repair started
...

The existing event and persistence systems should provide the foundation for this.

Replay should reconstruct observable workflow events rather than attempt to reproduce hidden model reasoning.


---

13. "What Changed?"

Goal

Provide a project-level summary.

Example:

What changed since I last opened AgentOps?

3 tasks completed
1 task failed
17 files changed
246 tests run
238 passed
8 failed → repaired

Agents:
Codex: 6 runs
OpenCode: 3 runs
Pi: 2 runs

Most active project:
projects-ai

This should be accessible from the project dashboard.


---

14. Project Overview

Each repository should have its own dashboard.

Example:

projects-ai

Health       ✓ Healthy
Branch       main
Last commit  4m ago

TASKS
12 completed
2 running
1 failed

AGENTS
Codex       8 runs
OpenCode    5 runs
Pi          3 runs

VERIFICATION
Tests       246
Passed      238
Failed      8

RECENT CHANGES
...

Requirements

Include:

repository status

current branch

active workflows

recent tasks

agent activity

verification health

recent commits

recent failures

pending approvals



---

15. Agent Health Page

AgentOps should understand the health of installed agents.

Example:

AGENTS

✓ Codex
  Available
  12 successful runs
  91% first-attempt success

✓ OpenCode
  Available
  8 successful runs

⚠ Pi
  Available
  3 consecutive failures

✗ Antigravity
  Not detected

For unavailable agents:

Why isn't Antigravity available?

Executable not found.

Expected:
agy

[ Configure ]
[ Rescan ]

Do not force users to interpret raw subprocess errors.


---

16. Automatic Setup Wizard

Goal

Make first launch easy.

Example:

Welcome to AgentOps

Let's get you running.

✓ Python detected
✓ Git detected

Agents found:

✓ Codex
✓ OpenCode
✓ Pi
✓ Antigravity

○ Claude Code

[ Continue ]

Then:

Choose your defaults

Implementation: [ Auto ▾ ]
Review:         [ Auto ▾ ]
Verification:   [ Standard ▾ ]

[ Finish Setup ]

Principle

Zero-config should be the default.

Advanced configuration should remain available but should not be required for normal usage.


---

17. Project Onboarding

Provide:

agentops init

AgentOps should inspect the repository and detect things such as:

Python project
pytest detected
ruff detected
Git repository detected
Type checking: mypy detected

Then recommend:

Recommended verification profile:

Python Standard

✓ pytest
✓ ruff
○ mypy

[ Accept ]
[ Customize ]

The system should automatically detect common project tooling where possible.


---

18. Smart Notifications

Do not notify the user about every internal event.

Notify primarily when:

✓ Task completed
✗ Task failed
⚠ Approval required
⚠ Agent unavailable
⚠ Verification failed

Optionally notify:

AgentOps is still working...

Could later support desktop notifications.

Notifications should be configurable.


---

19. Cost and Resource Dashboard

Even if AgentOps is primarily local, users may use multiple cloud and local models.

Example:

TODAY

Runs             17
Agent time       42m
LLM requests     83
Estimated cost   $0.82

BY AGENT

Codex            $0.41
OpenCode         $0.27
Pi               $0.14

Requirements

Track provider-agnostic resource information where available:

execution time

model requests

token usage if available

estimated cost if available

local vs remote execution


Local models should be able to report:

$0

rather than requiring fake cost information.


---

20. Budget and Safety Controls

AgentOps should protect users from runaway workflows.

Example:

Run limits

Maximum runtime: 30 min
Maximum retries: 3
Maximum tasks:   20
Require approval before merge: ✓

The system should stop workflows when configured limits are exceeded.

Potential future limits:

maximum concurrent agents

maximum workflow depth

maximum repair attempts

maximum verification time

maximum total execution time

maximum estimated cloud cost



---

21. "Why Did It Fail?"

Failure information should be understandable.

Instead of:

FAILURE
PROCESS_ERROR
exit code 1

show:

WHY THIS FAILED

The implementation agent completed successfully.

Verification failed because:

tests/test_auth.py::test_expired_token
expected 401
received 200

AgentOps classified this as:

TEST_FAILURE

Recommended action:

→ Repair implementation

[ Fix Automatically ]
[ View Test ]
[ Retry ]

Requirements

The failure view should distinguish:

agent failure

process failure

timeout

cancellation

test failure

lint failure

type-check failure

build failure

dependency failure

environment failure

Git conflict

policy violation

review rejection


The existing failure kernel should power this UI.


---

22. Recovery Center

Create one place for unresolved problems.

Example:

RECOVERY CENTER

3 problems need attention

────────────────────────

✗ Run #192
Test failure
Recommended: Repair

⚠ Run #188
Merge conflict
Recommended: Review changes

⚠ Run #181
Agent timeout
Recommended: Retry with another agent

Provide one-click recovery actions.

Recovery should remain backed by the existing persisted recovery model.


---

23. Global Search

Provide search across:

tasks

runs

files

failures

commits

agents

logs

artifacts

workflows


Example:

Search AgentOps

"authentication"

Possible future natural-language search:

> Show me every failed authentication-related run from the last week.



Search should be backed by indexed metadata rather than requiring full log scans for every query.


---

24. Command Palette

Add:

Ctrl+K

Example:

Search commands...

> New task
> Open project
> Start workflow
> View running agents
> View failures
> Retry run
> Review changes
> Open settings
> Switch project

This should be a lightweight navigation/action layer.


---

25. AgentOps Doctor

Provide:

agentops doctor

and a corresponding GUI system-health view.

Check:

✓ Python
✓ Git
✓ SQLite
✓ Agent registry
✓ Codex
✓ OpenCode
✓ Pi
✓ Antigravity
✓ Worktree support
✓ Verification environment
✓ Database migrations
✓ Permissions

For failures, explain:

what is wrong

why it matters

how to fix it

whether AgentOps can fix it automatically


This should become the primary diagnostic tool instead of making users interpret stack traces.


---

26. Task Templates

Provide templates:

New Task

Templates

[ Bug Fix ]
[ Feature ]
[ Refactor ]
[ Tests ]
[ Documentation ]
[ Dependency Update ]
[ Code Review ]
[ Full Project Task ]

Example:

Bug Fix

Automatically create something conceptually similar to:

Explorer
→ Implementer
→ Tests
→ Reviewer

Templates should compile into normal AgentOps workflows.

Do not create a second orchestration mechanism for templates.


---

27. Workflow Presets

Provide simple presets.

Fast

1 agent
minimal verification
no review

Standard

planner
implementer
tests
reviewer

Thorough

planner
parallel implementation
tests
security review
code review
final verification

Autonomous

maximum retries
automatic repair
automatic verification
approval before merge

The presets should be configurable.

They should map onto the existing workflow/task model.


---

28. AgentOps Inbox

This should become a central human-attention interface.

Everything requiring human action appears here.

Example:

INBOX

3 items

⚠ Approve merge
  Add OAuth support

⚠ Choose recovery
  Tests failed after 3 attempts

⚠ Agent unavailable
  OpenCode stopped responding

The user should be able to deal with these items without searching through individual runs.

Core principle:

> Don't make the user operate the orchestration system. Make the system operate the orchestration system.




---

29. Project Activity Graph

Eventually provide a graph of the relationships between tasks, agents, verification, repairs, reviews, and merges.

Example:

PROJECT

       ┌──────────┐
       │ Task 184 │
       └────┬─────┘
            │
     ┌──────┴──────┐
     ↓             ↓
  Codex         OpenCode
     │             │
     └──────┬──────┘
            ↓
        Verification
            │
       ┌────┴────┐
       ↓         ↓
     Repair    Review
       │         │
       └────┬────┘
            ↓
          Merge

This should visualize the existing execution relationships.

It should not become a separate source of truth.


---

Recommended Implementation Roadmap

Do not implement all features at once.

Prioritize them.

Phase 1: Make AgentOps Feel Like an Application

Implement:

1. New Task wizard


2. Mission Control dashboard


3. Live activity feed


4. Run detail/timeline


5. Human approval screen


6. Diff viewer


7. Failure explanation


8. One-click retry/repair



Phase 1 success criteria

A new user should be able to:

1. Open AgentOps.


2. Select a project.


3. Enter a task.


4. Start it.


5. Watch progress.


6. Understand failures.


7. Approve important actions.


8. Inspect the diff.


9. See the final result.



They should not need to manually construct a workflow.


---

Phase 2: Make AgentOps Smart

Implement:

9. Automatic agent selection


10. Agent profiles


11. Verification presets


12. Project auto-detection


13. AgentOps Doctor


14. Recovery Center


15. Task templates


16. Command palette



Phase 2 success criteria

AgentOps should automatically determine most reasonable defaults.

The user should primarily override behavior only when they have a specific reason.


---

Phase 3: Make AgentOps Powerful

Implement:

17. Visual workflow builder


18. Run replay


19. Global search


20. Cost/resource tracking


21. Budgets


22. Project analytics


23. Agent health


24. Notifications



Phase 3 success criteria

Power users should be able to inspect, customize, search, replay, and optimize complex multi-agent workflows without losing the simplicity of the default experience.


---

Architecture Guidance

These UX features should build on the existing architecture.

Do not rewrite the project merely to implement the UI.

The existing components should remain the underlying engine.

Conceptually:

┌─────────────────────────────────────────────┐
│                  AgentOps UI                │
│                                             │
│ Dashboard │ Tasks │ Runs │ Inbox │ Projects│
└──────────────────────┬──────────────────────┘
                       │
                       ↓
┌────────────────────────────────────
