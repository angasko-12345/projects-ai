# Pending Tasks

> Rewritten 2026-10-02 to be the **live task queue**. It contains ONLY unfinished work.
> The live bug ledger is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` section 0;
> verify a status there before starting anything on this page.
>
> Completed history stays in `.agents/memory/project.md`. The 2026-09-29 audit fix queue
> (master-bug-synthesis.md section 9) is superseded and must not be used as a queue.

## AgentOps -- open

| ID | Item | Evidence |
|---|---|---|
| ROOT-034 (AgentOps share) | `DBG-12` residual test debt; `DBG-15` residual migration-test debt | `DBG-08` (no real-object process-tree test) CLOSED 2026-10-03 by `tests/test_runtime.py::test_real_parent_terminate_process_kills_descendant_tree` — real parent→child→grandchild, mutation-proven, verified at 444 tests, 4 environment skips, OK. No item in the ROOT-034 cluster is a new production bug. Row stays open only for the DBG-12/DBG-15 residual test debt -- see master-bug-synthesis section 0 |
| -- | `SpawnFactory` Protocol typing (deferred from the A3 review) | still deferred |
| -- | Repository characteristics for routing are inert | `workflow.py` still passes `{}` as repository characteristics, so router repository-derived signals do nothing |
| -- | A8 reviewer pass | A8 implementation is DONE; the read-only opencode/copilot review pass was never run |

## AgentOps -- held / requires decision

| Item | Why held |
|---|---|
| ROOT-033 -- Windows termination classification | No Windows termination contract exists. Do not implement the high-bit heuristic. Needs an explicit platform decision first |
| D5 follow-up -- GitHub Actions under the local-only repo policy | CI itself shipped at `2cb2413`; what remains undecided is whether it should gate PRs, which needs a user decision on secrets/auth |
| D6 -- exe rebuild cadence after each Track A milestone vs batched | standing constraint, unresolved |

## Universal-game-agent -- open

| ID | Item | Evidence |
|---|---|---|
| ROOT-034 (UGA share) | `DBG-06` and `DBG-07` CLOSED (`59f5a1b`); `DBG-13` closed; `DBG-14` DISPROVEN; `DBG-12` residual shared test debt only | DBG-06 (direct test execution ineffective) and DBG-07 (four tests writing checkpoints into the caller's CWD) were CLOSED 2026-10-03 in `59f5a1b`, verified at 318 tests, 1 skip, OK; `tests/test_cwd_isolation.py` is the standing DBG-07 regression guard. `DBG-13` is FIXED for routing with only residual fixture quality (test debt); `DBG-14` is DISPROVEN. Nothing on the UGA side is an open production bug. Row stays open only because the DBG-12/DBG-15 test debt remains (DBG-08 closed 2026-10-03) -- see master-bug-synthesis section 0 |
| -- | STEP 3 exp02 re-run (post-fix validation run) | the pre-flight ran 2026-10-02 and the detector bands hold (0 steps in the ambiguous 200..300 gap, MISS band exact, no window-chrome red above the hit band), so the re-run is now unblocked. The re-run itself did NOT happen: cancelled because the machine was in use. The exp02 verdict stays suspended. Ask before starting a long GUI run -- it sends real `SendInput` keystrokes and holds a real window for three phases. Read the cadence caveat in `universal-game-agent/AGENTS.md` first: at 60 ms hold + 80 ms delay the paddle moves 5 px per decision against ~34 px of ball travel, so a 100-episode hit/miss comparison has very few hits to compare |
| -- | Untested `training/external_experiment.py` orchestration | helpers covered, the three-phase driver is not |

## mini-llm -- open

| ID | Item | Evidence |
|---|---|---|
| -- | Checkpoint stores default paths | `Config.tokenizer_path` has no CLI flag and `prepare_data.py` does not record it in `meta.json`, so every checkpoint stores the default `data/tokenizer.json`; `train_bin`/`val_bin`/`checkpoint_dir` come from the checkpoint on resume. Reproduced: bare `--resume` of the TinyStories artifact dies with `vocab_size=8192 but data/processed/train.bin was encoded with a vocabulary of 308`. Source-level wart, not fixed |
| -- | 3 `TestGenerationSeed` errors | caused by the working-tree deletion of `data/tokenizer.json` (pre-existing user change, deliberately left untouched). Clears with no code change if the user restores the file |
| -- | Full 1.9 GB TinyStories prep + GPU run | needs a cloud GPU, not this box; procedure is in `docs/EXPERIMENT-tinystories.md` |

## Frozen

Frozen by deliberate decision. **Re-entry requires an explicit user decision.** It is not a
backlog item and must not be picked up as ordinary work.

| ID | Item | Freeze note |
|---|---|---|
| A5 | WorkflowEngine decomposition | FROZEN 2026-10-01. The stated entry condition (close the correctness queue) HAS been satisfied, but the freeze remains by deliberate decision. Rationale + criteria in `.agents/plans/architecture-freeze-a5-a6-a7.md` |
| A6 | First-class `ReviewRun` / `MergeRun` | FROZEN 2026-10-01, same condition and same deliberate decision. No `ReviewRun`/`MergeRun` classes exist; this is a design task first, not a split |
| A7 | StateStore repository split | FROZEN 2026-10-01, same condition and same deliberate decision |

## Not started

| ID | Item |
|---|---|
| A9 | Artifact lifecycle + orphan recovery |
| B6 | Policy gates |
| B8 | Approvals (human gate, first-class) |
| B9 | Project memory (per-repo conventions) |
| B10 | REST API + evals |
| -- | Track C UX (C1/C2/C3) |

## Superseded / historical pointer

- `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` section 9 "Authoritative
  Fix Queue" is **SUPERSEDED 2026-10-02**. Read section 0 of that file instead.
  Sections 1-11 remain as historical audit evidence.
- The other reports in `.agents/memory/opencode/bugfinding/` are dated audit evidence
  (`bug-registry.md`, `deep-bug-audit-2026-09-29.md`, `geminihandoff.md`,
  `projects-ai-bug-handoff.md`, `projects-ai-review-handoff.md`,
  `repo-review-2026-09-26.md`). They are not instructions and are never revised in place.
- ROOT-015..ROOT-025 and ROOT-028 are DISPROVEN and are not queue items.
- The Agent Intercom Windows EPERM fix, the AgentOps packaging/release work, the roadmap
  Phase 1+3 delivery, A4, A3, B7, the temp-archive and restore operations, and the A8
  implementation are all complete. Their history is in `.agents/memory/project.md`.
