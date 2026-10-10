# 2026-10-10 - mini-llm scaling, driven from OpenCode

Session record. The full task record is
`../../mini-llm/sessions/2026-10-10-scaling-stages.md`; the canonical decisions and lessons are
in `../../decisions.md` and `../../lessons.md`. This file only records the OpenCode-specific
operational notes from the session, so the two are not duplicated.

- Implemented and verified mini-llm scaling Stages 1-4 (CLI config, atomic checkpoints plus
  rotation, SDPA attention, gradient accumulation) and Stage 3 chunked loss. Stages 1-4 are
  committed (`c112b4a`, `dc98601`, `ac8a8ec`, `6ab4ced`); chunked loss is in the working tree,
  uncommitted, reported and stopped per the task.
- `C:` hit 0 bytes free mid-session, which made the whole suite error with `[Errno 28]`.
  Rerouted the run with `TEMP`/`TMP` set to `D:\tmp\mini-llm` and left `C:` untouched (shared
  machine; asking before deleting another agent's temp).
- The peak-memory benchmark ran from the pre-approved scratch dir
  (`C:\Users\admin\AppData\Local\Temp\opencode\bench_chunk.py`), never the repo.
- `rtk git -C <repo>` handled log/diff/status; `rtk` has no `cat`/`ls`/`rg` here, so reads used
  the Read tool and searches used `Select-String`.
