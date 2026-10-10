# Session — 2026-10-05 — AgentOps evidence contract and boundary audit

Scope: `agentops/` correctness only. Working tree shared with ohmypi/Pi, so
every commit here touched only AgentOps files and no `.agents/memory/` file
another writer had dirty.

## Starting state

Four local defects open, three uncommitted. `main` ahead of `origin/main`.

## What was fixed, in order

| Commit | Defect | Live or latent |
|---|---|---|
| `7fc6dc2` | `unittest discover` on a repo with no `tests/` reported failure, masking success as `nonzero_exit` | live |
| `dcaab06` | implementation PASSED on bare exit 0; `files_changed` was persisted on `AgentRunOutcome` but `RunResult` has no such field, so nothing read it | live |
| `e02ff24` | UNVERIFIED → BLOCKED verification entered the repair loop and invented a repair task for a failure never demonstrated | live, found by Luna reading source |
| `bcebea0` | `RunResult.succeeded` ignored `cancelled`/`terminated`, so a killed agent with exit 0 carried a task to PASSED | latent — no live path found |

## The evidence contract (`agentops/evidence.py`)

Scoped by role, because "investigate why X fails and report" legitimately writes
no files and a blanket no-changes-is-failure rule would be wrong:

```
implementation  WORKTREE_CHANGE   must change the tree
research        RESULT_TEXT       the answer IS the deliverable
architecture    RESULT_TEXT
review          RESULT_TEXT
verification    PROCESS           exit code is the evidence
debugging       PROCESS
```

The workflow measures the working tree itself rather than reading the persisted
run, so the check does not depend on which runner executed the task.

## Three states, not two

Collapsing two of them broke three unrelated tests:

- observed non-empty → satisfied
- observed **empty** → `no_evidence`, fails
- **could not observe** → satisfied, left to verification

`GitRunMetadataCollector` reads `git status --porcelain`, which prints nothing
outside a repository. Reading that as "nothing changed" fails every run against
a plain directory.

## The five-boundary audit

Three questions per boundary: what evidence authorises the stronger state, does
the deciding object hold it, does every weaker state stay distinct.

| Boundary | Result |
|---|---|
| process→agent | fixed in `dcaab06` |
| agent→task | fixed in `bcebea0` (`cancelled`/`terminated` read nowhere) |
| task→verification | fixed in `e02ff24` |
| review/READY | **clean** — `assess_workflow_readiness` already requires evidence co-located on the same PASSED+verified task; repair-cycle scoping prevents stale donation |
| merge/finalization | not audited |

`diff_stat`, `structured_result`, `stdout_path` are never read at any decision
point. Judged correct: they are diagnostics, not evidence.

## Honest limits of this session

- `files_changed` is **minimum evidence, not proof**. An agent can write
  `garbage.txt` and satisfy the predicate; verification is the layer that
  catches it, and on a repo with no applicable tests verification is UNVERIFIED
  and proves nothing. Left alone deliberately — the next evidence layer exists,
  and closing this now would add a second proof system before the first has
  demonstrated it needs one.
- The structured `SKIPPED` reasons (`inapplicable` / `fail_fast` /
  `cancelled_before_start`) are still not surfaced in transcripts. Observability,
  not correctness. Deferred three times on purpose to avoid stacking unreviewed
  changes.
- Six commits remain **local only**; the push was deferred each session because
  other agents were working. Nothing is backed up off this machine.

## Verification actually performed

- Every fix: test written first, watched fail for the right reason, then pass.
- Full suite after each commit: 530 → 536 passed under pytest, 540 under
  unittest, 4 skipped.
- Each fix proved to bite by reverting only the changed lines and confirming the
  targeted test went red, then restoring.

## Procedure worth reusing

**Call the new or edited function once, directly, with a known answer, before
running any test.** Then once with the boundary case. This caught three silent
failures in one session — a guard using `subprocess`/`sys` without importing
them, an argv index off by one, and a field set on the wrong dataclass — each of
which returned a plausible wrong answer instead of raising. See `lessons.md`.

---

# Addendum — boundary 5 and the CI failure

## Boundary 5 (merge/finalization), commit 98ea451

Measured before changing anything, against real state:

| Case | Setup | Before | After |
|---|---|---|---|
| A | verification PASSED, review PASSED | merged | merged |
| B | verification BLOCKED | **merged** | committed + preserved |
| C | verification **FAILED** | **merged** | committed + preserved |
| D | `garbage.txt` + BLOCKED | **merged** | committed + preserved |

The gate was `result.ready or implementation_passed`. Neither operand consults
the verification task, so a demonstrated FAILED verification and a merely
unproven change were indistinguishable — both merged. Case C was the
non-negotiable one.

Operator's decision — **limbo**: not thrown away, not merged.

```
not READY -> commit on the agentops/* branch, PRESERVE the worktree
READY     -> commit and merge
```

Case D settled the garbage-file question with **no new detection**: the gate
refuses everything except case A.

Two entry points had disagreed (CLI merged on `ready or implementation_passed`,
the GUI only on `result.ready`); both now call one shared
`finalize.finalize_for_outcome`, so they cannot drift. Added
`agentops retry-merge <worktree>` — `retry_merge` was GUI-only, so preserved work
had no terminal route from a terminal. Verified end to end with real git.

## CI: 15 failures that did not exist locally

Commit `40f963c`. Reproduced on a pristine worktree at `origin/main`: exactly 15,
all in `test_workflow.py`, all mine. Fixed by declaring evidence explicitly in
`WorkflowTests.setUp` — verified **15 -> 0** on a clean tree.

**Still failing in CI** on ubuntu/py3.11, cause unconfirmed: the run-logs API
returns 403 unauthenticated and `gh` is not installed, so the failing test names
could not be read. Prime suspect is the ~45 POSIX-only tests that Windows skips.

## Unfinished / honest limits

- CI red on `main` at time of writing; diagnosis blocked on log access.
- `CliServices` dependency-injection refactor (`cli.py`, `test_cli.py`, ~1500
  lines) belongs to another agent, uncommitted, and **breaks the live tree's
  unittest run** (16 failures in `test_cli.py`). Not mine to fix or commit.
- Structured `SKIPPED` reasons shipped in display form (`68cb38c`); the
  underlying structured skip taxonomy in transcripts is still not done.
- 26 files left uncommitted across `.agents/memory/`, `tiktok-slop-factory/`, and
  `small-projects/` — other agents' work.

