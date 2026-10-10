# Buffy Agent Memory

> My persistent working memory as Buffy (Freebuff / Laguna S 2.1). Update as I discover verified facts. Canonical sources: `.agents/AGENTS.md`, `.agents/pi_AGENTS.md`, `team.md`, and live source/tests/git history. Current code/tests/git history outrank all memory.

## Agent identity

- **Name:** Buffy — the coding agent behind Codebuff, running on the Laguna S 2.1 model inside Freebuff.
- **Scope:** All products in this repository (`agentops/`, `universal-game-agent/`, `small-projects/mini-llm/`) plus `tiktok-slop-factory/`. Work only within the assigned task and repository scope.
- **Single-writer rule:** Either Pi or Oh-My-Pi is the sole writer in a dirty tree. Reviewers are read-only. Do not make uncoordinated edits to files another session owns. Stage by explicit path list, never `git add -A`.
- **antislop:** Load for UI, copy, people, mobile layout, or code-comments work. Ask the user whether it applies during the work or after. Not relevant for documentation-only tasks.

## Repository structure

Container at root (`D:\admin\code\projects`), three independent Python products plus tooling:

| Product | Purpose | Test command (from its own dir) |
|---|---|---|
| `agentops/` | Local-first orchestrator for coding-agent CLIs; SQLite persistence, GUI, PyInstaller exe | `cd agentops && python -m unittest discover -s tests` |
| `universal-game-agent/` | RL Pong agent: Gymnasium toy path + Windows-only external screen-capture/SendInput path | `cd universal-game-agent && python -m unittest discover -s tests` |
| `small-projects/mini-llm/` | GPT-style causal LM built from scratch with PyTorch | `cd small-projects/mini-llm && python -m unittest discover -s tests` |
| `tiktok-slop-factory/` | TikTok video rendering pipeline (separate sessions) | `cd tiktok-slop-factory && python -m pytest` (~594s, memory-hungry) |
| `.agents/` | Process layer: instructions, memory, skills, team | N/A |

**No cross-product imports.** Each product has its own `AGENTS.md`. There is no single repository-wide test command.

## Verified test baselines (as of 2026-10-05)

From `.agents/AGENTS.md` (canonical), confirmed by running individual test modules:

| Product | Baseline | Verified |
|---|---|---|
| `agentops/` | 512 tests, 4 skipped, OK | Confirmed by session logs (opencode packaging pass) |
| `universal-game-agent/` | 495 tests, 1 skip, OK | Confirmed: `test_num_envs` = 31 tests OK; full suite times out >90s due to external-game tests |
| `small-projects/mini-llm/` | 142 tests, 1 skip, OK | Canonical AGENTS.md; mini-llm/AGENTS.md says 146 (stale discrepancy — trust canonical) |

**Baseline drift warning:** Test counts are frequently updated in multiple files. Always re-run the suite rather than trusting recalled numbers. The antigravity memory file cites different UGA/agentops numbers (366, 501) — those are stale; canonical `.agents/AGENTS.md` is authoritative.

## Git state (current)

- HEAD: `ec0ded4` (docs: record post-consistency-validation mini-llm baselines), on top of `5c858c9` (num_envs task).
- Working tree is **dirty** from concurrent writers (antigravity, pi, opencode): changes in `agentops/agentops/cli.py`, `tiktok-slop-factory/`, `.agents/memory/*`. Do NOT stage or touch other writers' files unless my task requires it.
- **Root `.gitignore` exists and is tracked** — restored per `decisions.md:256` (supersedes the 2026-09-15 removal). It covers container-level patterns. Product-specific ignores live in each product's `.gitignore`.

## Conventions & constraints

### Git workflow
- Stage by explicit path: `git add <file1> <file2>`, never `git add -A` or `git commit -a`.
- Commit message via heredoc — **BUT heredocs fail on git-bash on Windows** (backslash-newline issues). Use single-line `-m` with escaped newlines instead:
  ```
  git commit -m "short summary.\n\nDetailed body.\n\nGenerated with Codebuff\nCo-Authored-By: Codebuff <noreply@codebuff.com>"
  ```
- Default shell timeout is 30s — increase for test suites (UGA full suite >90s, tiktok ~594s).
- Back up dirty tree before substantial work: `git diff` + tarball untracked files to `/tmp/agentops-backup-*`.

### SQLite
- Additive migrations only: `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN`, `INSERT OR IGNORE` for migration versions. Explicit column names on every insert.
- After touching migrations: update assertions in `tests/test_events.py:276` and `tests/test_failure_kernel.py:588`.

### Code
- Keep deterministic classification/parsing/verification logic in leaf modules (no SQLite, subprocess, GUI, or other I/O).
- Write a regression test before fixing a bug.
- Never persist raw prompts, secrets, credentials, tokens, or private keys. Reuse existing redaction mechanisms.
- No lint/typecheck/coverage commands — don't invent them.

### Windows environment
- Shell: git-bash (POSIX). Use forward slashes, `ls`/`mv`/`rm`/`cp`/`grep`, never `dir`/`move`/`del`/`copy`.
- Python: 3.11+ (agentops), 3.14.7 available (UGA, mini-llm).
- Avoid backslashes inside f-string expressions on Python <= 3.11 (SyntaxError).
- For frozen exe subprocesses: use `CREATE_NO_WINDOW` via `agentops/runtime.py:spawn_options()`.

## Memory hierarchy

1. Current source/test code — highest authority
2. Current git history (`git log`, `git show`)
3. Explicit user instructions
4. Canonical memory: `.agents/memory/project.md`, `architecture.md`, `decisions.md`, `lessons.md`
5. Agent-scoped: `.agents/memory/opencode/`, `.agents/memory/oh-my-pi/`, `.agents/memory/cline/`, `.agents/memory/mini-llm/`, `.agents/memory/antigravity/`
- `decisions.md` and `lessons.md` are append-only. Add dated entries; never rewrite old ones.
- The **live bug ledger** is `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` §0 (Current Status). Do not treat §9 or older sections as current.
- The **live task queue** is `.agents/pending_tasks.md` — contains only unfinished work.

## UGA architecture (relevant to tasks I work on here)

- Layer direction: `agent/` → `training/` → `environment/` → `interface/`. The agent module has no game-logic dependency.
- `training/ppo.py`: `PPOConfig`, `PPOTrainer`, `run_experiment` toy helper, `__main__` CLI.
- `training/experiment.py`: `make_env_for_training(make_env, num_envs)`, `run_experiment` YAML runner.
- `environment/vec.py`: `SyncVectorEnv` — takes factory callables, calls `make()` in `__init__`, no multiprocessing. Properties: `reset(seeds)`, `step`, `reset_env(index)`, `close`, `num_envs`.
- `environment/toy_pong.py`: `ToyPongEnv` (Gymnasium, cross-platform).
- `environment/external_game.py`: `ExternalGameEnv` (Windows-only, ctypes window management, SendInput).
- `configs/default.yaml`: loaded by bare `yaml.safe_load` — no schema validation, no inheritance. `PPOConfig.from_dict` warns on unknown keys.
- `main.py`: CLI with subcommands (smoke-test, train, evaluate, compare, experiment).
- PPOTrainer dispatches on `isinstance(env, SyncVectorEnv)`; vector path does `self.num_timesteps += slots` per step.

### num_envs task — COMPLETED (commit 5c858c9)
- `PPOConfig.num_envs` field (int, not bool, >= 1) with validation in `training/ppo.py`.
- `make_env_for_training(make_env, num_envs)` in `training/experiment.py`: single env when `num_envs==1`, `SyncVectorEnv([make_env]*num_envs)` when >1.
- `configs/default.yaml`: `ppo.num_envs: 1` with comment.
- `main.py:cmd_train`: both initial and resume paths use `make_env_for_training(make_env, ppo_config.num_envs)`.
- `--num-envs` CLI arg added to `__main__`.
- `tests/test_num_envs.py`: 31 tests (12 test classes), all passing.
- Smoke tests: `--num-envs 1` → 128 timesteps × 1, 2 episodes; `--num-envs 2` → 256, 16 episodes; `--num-envs 4` → 512, 32 episodes.
- Fixed: stray duplicate `env = SyncVectorEnv(...)` line; `tests/test_cli.py` `_experiment_stub` updated to include `make_env_for_training`.

## mini-llm key facts (from verified sources)

- Built from scratch: hand-written Transformer (`nn.Module`s), BPE tokenizer via `tokenizers` lib, AdamW + cosine LR.
- `data/tokenizer.json` is a **tracked artifact** — must stay in tree. Deleted locally 2026-10-04, restored from git.
- Checkpoint schema v2 with `checkpoint_metadata` block; `validate_checkpoint()` is the single gate.
- `weights_only=True`-safe (plain containers + tensors).
- Data provenance: sha256 digests in `meta.json` identify artifacts by content, not path.
- Resume: compares recorded digests against current artifacts; refuses on mismatch.
- `.gitattributes` pins `-text` on `data/` (LF vs CRLF changes BPE vocab: 308 vs 310).

## Known issues (not my tasks — flagged for awareness)

- UGA `main.py --resume` crashes with `IndexError` on finished checkpoints (`ppo.py:334` loop never runs → empty `history`).
- UGA checkpoints non-atomic (no schema_version, no RNG state); `external_experiment.launch_phase2_process` ownership transfer is fine but `train()` computes `fps` wrong on resume.
- UGA `games/` double-import hazard: no `__init__.py`, `extern_pong.py` mutates `sys.path` — breaks `isinstance`/class identity. Fix: add empty `games/__init__.py`, use relative imports.
- agentops `workflow.py` is a 918-line god object (A5 decomposition proposed but frozen).
- tiktok tests take ~594s, fail with OOM when run alongside other Python processes.
- Test baseline numbers drift between memory files — always verify by running.

## Useful commands

```bash
# From repo root, run one product's suite
cd agentops && python -m unittest discover -s tests
cd universal-game-agent && python -m unittest discover -s tests
cd small-projects/mini-llm && python -m unittest discover -s tests

# Focused test module
python -m unittest tests.test_num_envs

# Git: stage specific files only
git diff --stat HEAD
git add <file1> <file2>
git commit -m "summary.\n\nBody.\n\nGenerated with Codebuff\nCo-Authored-By: Codebuff <noreply@codebuff.com>"
```

## Decisions made during my work

*(append dated entries here as I make decisions)*

---

_Last updated: 2026-10-05_
