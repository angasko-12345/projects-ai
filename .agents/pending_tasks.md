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
| ROOT-034 (AgentOps share) | Remaining test-quality gaps in the DBG-06..15 cluster | PARTIALLY FIXED -- see master-bug-synthesis section 0 |
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
| ROOT-014 | Guard resume against a finished/empty history | `training/experiment.py:111-119` indexes `history[...]` unguarded |
| ROOT-036 | Fix eval metric counting and preserve seeds | `training/evaluate.py:42-45`, `environment/external_game.py:274` |
| ROOT-034 (UGA share) | Remaining test-quality gaps in the DBG-06..15 cluster | PARTIALLY FIXED -- see master-bug-synthesis section 0 |
| -- | STEP 3 pre-flight: check live-play baseline reds in the exp window, then re-run exp02 | unchanged top item; the exp02 verdict stays suspended until this runs |
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
