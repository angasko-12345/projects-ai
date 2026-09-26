# Full repository review — 2026-09-26

Read-only review of the whole container repo at `D:\admin\code\projects`. No files were
modified. Four specialist lanes: agentops product, universal-game-agent product,
agent-process layer, repo-wide hygiene sweep.

**Status of this file:** a findings dump, not a decision. Nothing here is approved. Read
`.agents/memory/decisions.md` for what is actually decided.

## Scope of the review

- 197 tracked files, 1,811,102 bytes (1.73 MB), 20 commits.
- 99 Python files (~22,498 lines), 71 markdown files (~7,667 lines).
- ~59 MB of untracked binary state on disk (checkpoints, dist/, logs, sqlite). 0 bytes tracked.
- Two independent products plus a hand-built agent-process layer.
- Neither product's test suite was executed. Recorded baselines (agentops 357/4 skipped,
  UGA 261) predate the current working tree and are not trustworthy.

**The tree moved twice during this review.** A parallel writer committed `6890c42`, then
began an in-flight `agentops/persistence.py` feature (6 modified files in
`agentops/agentops/`, plus untracked `persistence.py` and `tests/test_persistence.py`).
Findings 6, 15, and 16 below were observed live rather than inferred.

## Shape of the result

The code is in notably better shape than the process layer. The agentops verification
kernel, process runtime, and artifact store all survived inspection and are the strongest
code in the repo. Meanwhile almost every "current state" fact in memory has drifted from
the tree it describes, and the largest cluster of findings is instruction drift, not
defects.

---

## Do first

### 1. Tracked external-Pong results are measurement artifacts, not gameplay

`universal-game-agent/experiments/exp_external_pong_02-15516_results.json`: 82-83% of
episodes in *both* baseline and trained are length exactly 1 with 0 misses, and
`terminated_episodes=100`. A length-1 episode that terminates with zero misses is
impossible — `ExternPongTermination.terminated()` fires iff the MISS banner is visible
(`environment/extern_pong_rewards.py:77-78`) and the banner edge pays `-1` (`:58-59`).
Zero misses means `prev_banner` was already true, so `:60-61` returns `0.0`. The reset
landed on a terminal frame, the episode "ended" before playing, and it was scored 0.

That structural zero sits inside the reported `mean_reward` and `std_reward`, so the
headline `-0.15 -> -0.11` "learning" delta rests on roughly 17 informative episodes. The
untracked `exp_external_pong_01-14676_results.json` shows the same pattern (7 of 8). The
in-tree `reset_settle_timeout_s` fix (`environment/external_game.py:151,256-271,459-466`)
targets this but has regenerated no results file.

`checkpoints/README.md:20-24` currently cites the contaminated
`exp_external_pong_compare01_results.json` as the provenance for the only external weights.

**Action: regenerate both results files under the settle fix before citing any external
number.**

### 2. Leaked OpenCode key redacted in-tree, still in history, rotation never recorded

A key-shaped token is present in `.agents/memory/pi-opencode-free-tier-fix.md` at commits
`99e0be2, 65ef097, 7b78659, c057fed, 533a899`. The remote is
`https://github.com/angasko-12345/projects-ai.git`, 50 commits on `main`, HEAD `6890c42`.
Redaction landed in `5627490` and the working tree is clean.

Rotation is recorded in exactly one place —
`.agents/memory/opencode/sessions/2026-09-26-repo-reorganization.md:143` ("Rotate the
exposed API key") — a session log no instruction file requires reading.

No `.env`, `*.pem`, `*.key`, or `credentials*` file exists in the tracked tree, and a
regex sweep of all 19 files under `.agents/` returns only 3 hits, all `ses_`-prefixed
format-only session identifiers that the repo's own probes document as non-credentials.
The residual exposure is git history plus missing rotation, not a live leak.

**Action: rotate the key, add a dated `decisions.md` entry recording the rotation (or
explicitly recording "not yet rotated"), and add one sentence to the secret rule — a
credential in history is compromised until rotated, and the rotation must be logged.**

### 3. `agentops task` fails on any target repo that has not hand-patched its `.gitignore`

`agentops/agentops/git.py:100` creates worktrees at `<repository>/.agentops/worktrees/<branch>`,
inside the target repo. `git.py:239-243` then refuses to merge when the base
`git status --porcelain` is non-empty — and `?? .agentops/` is exactly that. Nothing in
the package writes or verifies a `.gitignore` entry; there is no `gitignore` or
`check-ignore` reference anywhere under `agentops/`.

`agentops/README.md:67` claims the state is stored under `<target-repo>/.agentops/` "and
ignored by Git", which is false for arbitrary targets. `agentops/.gitignore:1` hides the
bug in development.

**This is the primary product path. It is broken.**

### 4. The custom-workflow feature bypasses the review gate

`agentops/agentops/execution_model.py:49-50` and `assert_workflow_ready`
(`:186-201`) define READY as passed+verified verification **and** a passed review **and**
evidence. `workflow.py:1143` and `:1169` enforce that for the standard flow.

The custom-DAG path does not. `cli.py:391-393` computes
`ready = state.refresh_workflow_status(workflow_id).value == "passed" and bool(evidence)`,
and `state.py:863-874` returns PASSED whenever *all* tasks are PASSED, with no role
inspection. A custom workflow containing a verification task but no `review` task reaches
`ready=True`, then `cli.py:417-419` calls `finalize_worktree` and merges.

### 5. Raw agent stdout/stderr reach SQLite unredacted while the same bytes are redacted on disk

`agentops/agentops/workflow.py:558` sets `task.result = f"log={result.log_path}\n{result.stdout}\n{result.stderr}"`;
`state.py:583-593` persists `result` verbatim. The verification paths do the same at
`workflow.py:515` (`report.transcript`) and `:524` (legacy command output).

Every other sink redacts: `logging.py:67-69` for log files, `artifacts.py:166` for text
artifacts, `workflow.py:206-207` and `:282` for failure evidence. SQLite is the only sink
with no redaction, and it is the sink the CLI (`cli.py:157-171`) and GUI (`gui.py:481-482`)
read back and print.

This contradicts `.agents/AGENTS.md:64` directly. The in-flight A8 change set does not
touch this path.

### 6. No CI exists

`.github/` contains exactly one file, `copilot-instructions.md`. There are no workflows, no
actions, no dependabot, no templates. Verified absent repo-wide: `.pre-commit-config.yaml`,
`Makefile`, `justfile`, `tox.ini`, `noxfile.py`, and every other CI config.

Consequences: zero automated execution of both suites; no runner OS; no Python version
matrix; no dependency-install step; no coverage; no secret scanning. The "write a
regression test before fixing a bug" rule has no enforcement.

Both products are Windows-constrained — agentops packaging is Windows-only
(`runtime.py:102-112` `CREATE_NO_WINDOW`, `AgentOps.spec`, `pyproject.toml` extra
`windows = ["pyinstaller>=6.0"]`) and UGA's external path is Windows-only by construction
(`ctypes` window management, `SendInput`, MSS). No Linux job could pass UGA's external
tests and no agentops packaging check could pass off-Windows, so a matrix must be
`windows-latest` for both.

**"No single repository-wide test command" is a two-row matrix, not a blocker.**

### 7. Every ignore rule lives in an uncommitted, machine-local file

There is no root `.gitignore`. `.git/info/exclude:7-17` is the only thing ignoring
`.misc/`, `.playwright-mcp/`, `.playwright_mcp/`, `agent-intercom-fix/`, `.agentops/`,
`.local-temp-archive/`, `omniroute-model-results.csv`, `working_models.txt`, `.pi/`, and
`small-projects/`. `git check-ignore` confirms these bind to this clone only.

A fresh clone has zero ignore rules. `.agentops/state.sqlite` (196,608 bytes) plus 9 agent
log files, 5 `.playwright-mcp/console-*.log`, 18 `.playwright-mcp/page-*.yml`, and the
directories `.pi/`, `.misc/`, `small-projects/`, `agent-intercom-fix/` all become untracked
noise on any other clone — including `working_models.txt` and
`omniroute-model-results.csv`.

`lessons.md:366` and `opencode/lessons.md:60` both have to teach "never `git add -A`" —
a rule forced by a file that is not in the repo.

**Tension to resolve first:** `decisions.md:219-224` records a deliberate 2026-09-15
decision to remove the root `.gitignore`. Adding one back contradicts a recorded decision.
Decide which rule wins and log it; do not just add the file.

### 8. `tasks/after-task.md` is mandated universally, is agentops-only, and is stale

`.agents/AGENTS.md:32`, `.agents/pi_AGENTS.md:19`, and `.agents/ohmypiagents.md:109` all
send every agent to it. It is 115 lines with zero occurrences of `universal-game-agent`.
`:23-24` hardcodes `cd agentops` plus `python -m unittest discover -s tests`, and
`:55-69` is entirely exe packaging (`:59-60` taskkill `dist/AgentOps.exe`, `:62` rebuild).

A UGA task following it runs the wrong suite and packages a product it never touched.
`:27-28` states the baseline as "224 passing, 3 environment skips as of 2026-09-15" —
roughly 130 tests stale against the 357 total / 4 skipped recorded in `.agents/AGENTS.md:49`.

### 9. UGA checkpoint and resume are non-atomic, non-reproducible, and crash

`universal-game-agent/training/ppo.py:290-303` writes `torch.save(..., path)` straight to
the final name — no temp file plus `os.replace` — so a crash mid-write leaves a truncated
`.pt` that `external_experiment.py:332` then advertises as the run's only weights. Nothing
in the payload stores RNG state (`:293-302`), so `--resume` is not reproducible.

`main.py:185-190` loads `trainer.env_config` from the checkpoint and never compares it to
`cfg["env"]`, so resuming with an edited config trains old weights in a new environment
with no warning. And `train()` computes `fps = num_timesteps / (perf_counter() - start)`
(`:332,344`) with a restored cumulative numerator against a fresh start time, inflating
`history["fps"]` on every resumed run. `README.md:126` already concedes there is no schema
version.

`main.py train --resume` on a finished checkpoint raises an unhandled `IndexError`: if
`trainer.num_timesteps >= total_timesteps`, the loop at `ppo.py:334` never runs, `history`
stays all-empty, and `main.py:203-205` indexes `history['mean_reward'][-1]`. Same shape in
`training/experiment.py:111-118` and `external_experiment.py:322`.

### 10. Eval hits and misses are guessed from reward sign; the external env discards its seeds

`universal-game-agent/training/evaluate.py:42-45` counts `reward > 0` as a hit and
`reward < 0` as a miss. That holds only when every nonzero reward is strictly +/-1. It
silently miscounts any `composite`, survival, or curiosity-scaled run. Under `extern_pong`
the red latch clears only on re-serve, so `mean_hits` is *per rewarded latch, capped at 1
per episode* — 2 total hits across 100 baseline episodes, 7 across 100 trained.

Separately, `ExternalGameEnv.reset` (`environment/external_game.py:274`) accepts `seed` and
drops it — no `super().reset(seed=seed)` — while `external_experiment.py:231` and
`main.py:267` carefully record `seeds=0..99`. Those seeds are decorative. All episodes
share one live process RNG stream and real-time scheduling, so `main.py:306-313`'s
sign-only verdict has no controlled comparison behind it.

---

## Instruction-layer drift

The largest cluster by count, with one repeating pattern: a documented fact goes stale,
someone patches one copy, and the patch becomes a new stale fact.

| # | Finding |
|---|---|
| 11 | **The `ppo_final.pt` inventory is wrong, and the "correction" was itself wrong.** `universal-game-agent/AGENTS.md:104-108` says exactly two exist. There are five: `checkpoints/ppo_final.pt` (2,189,359 B), `checkpoints/ppo_untrained.pt` (4,126,135 B), and three nested `ppo_final.pt` at 12,378,159 B each (`extern_pong_01/`, `extern_pong_01/run-14676/`, `extern_pong_02/run-15516/`). `lessons.md:350` records that a "four files" claim was flagged among seven false claims and "corrected" to two — the correction was itself wrong and propagated into `checkpoints/README.md:8`. Agents are told to "identify a checkpoint by directory, never by filename alone" (`:108`) on a false inventory. |
| 12 | **Three conflicting UGA test baselines, one created by the file written to prevent exactly that.** `universal-game-agent/AGENTS.md:34` = 261 tests; `.agents/memory/oh-my-pi/project.md:11` = "268/268 passing" and `:51` = "Suite 273 OK" in the same file; `tasks/after-task.md:27` = 224. `lessons.md:355-360` is a lesson titled "Hardcoded test baselines rot within hours" whose prescribed fix was to date every number. |
| 13 | **`architecture.md:80` still names the retired Intercom roster as canonical message targets** — `opencode-arch`, `codex-builder`, `agy-reviewer` — which `team.md:22,68` states must never be tasked. This is the single most likely place an agent copies a forbidden session name. `:81` leaves Supermemory MCP availability as an open question. |
| 14 | **The startup checklist routes every agent to the AgentOps memory set.** `.agents/AGENTS.md:33` and `.agents/pi_AGENTS.md:20` name `project/architecture/roadmap/decisions/lessons.md`; all five are 100% AgentOps (`roadmap.md` alone is 334 lines of AgentOps Track A/B/C). The UGA set is `.agents/memory/oh-my-pi/` (41-line roadmap, 40-line architecture). A UGA-only session loads 461+ irrelevant lines. `architecture.md:104-112` admits the split is "planned, not implemented". |
| 15 | **`project.md:33` and `roadmap.md:22` state the pre-2026-09-26 collaboration policy as a live constraint** — "collaborate only with opencode, free-claude-code, and copilot" and "Single writer (Pi)". Both contradict `.agents/AGENTS.md:61-62` and `team.md:11-15`. The same defect is currently being patched out-of-band in a dirty working tree (`tasks/after-task.md:39-46`, uncommitted) with no `decisions.md` entry, one file at a time — exactly the pattern `lessons.md:362-367` documents going wrong. Fix all four in one decision-logged commit. |
| 16 | **`.agents/memory/` is a three-way fork** (`memory/`, `memory/oh-my-pi/`, `memory/opencode/`) duplicating 5 filenames (`lessons.md`, `architecture.md`, `roadmap.md`, `project.md`, `decisions.md`) with no authoritative index. 3 of the 7 currently-dirty files are parallel edits to those duplicates. |
| 17 | **`audit.md` is a v0.1.1 doc (14 modules, 79 tests) still routed to by a read-before-work rule** (`team.md:40`). Its dependency map (`:8-21`) omits 9 real modules. `:89` — "Redaction is heuristic (misses `Bearer`, multiline secrets)" — documents a defect fixed on 2026-09-16 (`decisions.md:196`). |
| 18 | **The pi free-tier runbook's own probe provably cannot work.** `pi-opencode-free-tier-fix.md:4` says the free tier works only on `/zen/v1/responses` because chat 503s, while `:5` says chat 403s. The probe at `:58-62` posts to `/zen/v1/chat/completions` with `"stream": false` — the exact shape `lessons.md:344` and `opencode/free-tier-gate.md:45-46` record as 403, and `lessons.md:345` states the earlier conclusion was wrong. |
| 19 | **`path:line` anchors in governance files are decaying claims.** Three of `universal-game-agent/AGENTS.md`'s Known-defects anchors no longer resolve: `:21` cites `external_game.py:363` (that is the `make_external_env_from_config` signature; dispatch is at 370/374/379/394-395/412/414), `:52` cites `external_experiment.py:112,118` (`:112` is `        cmd,`, `:118` is blank; `_results_path` is at `:202`), `:155` cites `external_game.py:369,414-426` (`:369` is a docstring continuation; the `extern_pong` hardcode is at 399-405 and 445-455). The same file already uses the better convention at `:169` (`external_game.py:_build_action_table`). |

---

## Add

Highest value first.

1. **A CI matrix.** `agentops` on windows/py3.11 (stdlib-only), `universal-game-agent` on
   windows/py3.12 plus torch. Closes findings 6 and 8 and the drifting-baseline problem.
2. **A committed root `.gitignore`** for the machine-local entries. See the decision
   tension in finding 7 — resolve that first.
3. **A degenerate-episode guard** in `evaluate()` or the results writer: fail or loudly
   annotate when more than 20% of episodes are length 1 with 0 misses. That single
   assertion would have caught finding 1 at write time.
4. **A "machine-local paths" section in `.agents/AGENTS.md`** listing what exists only on
   this clone because ignore rules are uncommitted. Closes the gap behind findings 7, 16,
   22, and 23 — an agent told to "stage explicit paths only" currently has no list of paths
   to avoid.
5. **A `verified-at: <commit> <date>` header on every memory file**, plus a check. Turns
   `lessons.md:355-360` from advice into something enforceable and kills findings 11-12.
6. **agentops regression tests for the two live bugs:** a temp repo with no `.gitignore`
   asserting `merge()` either succeeds or names `.agentops`; and an assertion that
   `workflow <file>` cannot reach `ready=True` without a passed `review` task.
7. **UGA checkpoint hardening:** a `schema_version` key, `torch.get_rng_state()` /
   `np.random.get_state()` / `random.getstate()`, and an `env_config` **comparison** on
   resume that errors when the live config differs. Plus a shared atomic-write helper
   (`tmp` + `os.replace`) for `ppo.py:save_checkpoint` and the results writer.
8. **Provider-reported event counters** so `evaluate()` stops inferring hits/misses from
   reward sign — `RewardProvider.events(prev, cur) -> dict`, falling back to sign only when
   absent.
9. **agentops test coverage for the untested surface:** a `test_gui_controller.py` (786
   lines, ~30 serialization methods, a recovery-scope bug, zero unit coverage); real CLI
   coverage (`tests/test_cli.py` is 19 lines against a 441-line entry point, 2 of 12
   subcommands); and a migration-contract test proving a v1 database upgrades additively
   without rewriting task history.
10. **A `NOTICE` / attribution file.** The repo is GPL-3.0 and vendors 194 KB of
    externally-sourced skills with no upstream license and no attribution.
11. **A root `README.md`** (~30 lines): purpose, the two products with their one-line test
    commands, the meta layer's role, license. A two-product public container with a
    GitHub remote and a licence currently has no human entry document.
12. **A rule that governance `path:line` anchors are decaying claims** — re-anchor on
    product change, or use symbol names as `universal-game-agent/AGENTS.md:169` already
    does.
13. **A rule that ephemeral Intercom session names never belong in any file.** They are
    currently baked into `architecture.md:80`, `lessons.md:68,75,82,144,152`,
    `decisions.md:145,152`, and `team.md:17,21` — and one of those is now an explicit
    "never task this" name.
14. **A "what exists but is not on the read path" list.** `pending_tasks.md`,
    `additions.md`, `audit.md`, `outputs/phase-2-*`, and `plans/chatgpt_*` are referenced
    by zero instruction files. Either name them in the checklist or archive them.
15. **A `test_results_contract.py`** asserting every field in a report matches the config
    that produced it.
16. **An encoding requirement in `opencode/environment.md`.** PowerShell 5.1's default
    `Get-Content` decodes these UTF-8 files as ANSI; one lane nearly filed a false
    corruption finding because of it. Specify `-Encoding UTF8` for all file reads.

### Deliberately not recommended

A linter, formatter, or type-checker. `.agents/AGENTS.md:55` says "do not invent lint,
formatter, type-check, or coverage commands", and all four instruction files state
consistently that none is configured. Verified: no `ruff.toml`, `.ruff.toml`, `mypy.ini`,
`.flake8`, `pytest.ini`, `setup.cfg`, `.pre-commit-config.yaml`, and no `[tool.*]` beyond
setuptools. If you want one, that rule has to change first — do not just add a `ruff.toml`.

---

## Improve

### agentops

- Split `ready_tasks()`'s writes out of a query named as a read (`state.py:846-861` marks
  tasks BLOCKED, calls `update_task`, and emits `task-updated`; called in a loop from
  `workflow.py:378`).
- `workflow.py:1143` and `:1169` — `assert_workflow_ready` is invoked inside the `if`
  whose condition is the same three booleans, so it can only fail because the `if` is
  wrong. Only `evidence_present` adds information.
- `gui.py:462,485,504,640,670` — five `getattr`/`callable` probes against a `GuiController`
  Protocol (`gui.py:26-56`) that already declares those methods. No test exercises them.
- Enforce the `MUST_FAIL_CLOSED` table. `persistence.py:47-48` states the policy and
  `:63-70` marks `task.update` and `merge.conflict_task`, but `DegradationRecorder` is
  consumed only by `verification_kernel.py:19,104`. `workflow.py:289-292` leaves
  `recover_incomplete`'s `create_failure` as bare `except Exception: pass`, and
  `:580,595,608,673,701` wrap `record_failure` the same way. `state.update_task` — the
  write that makes `verified=True` a durable claim — is never guarded, so the strictest
  policy in the table currently protects nothing.
- `gui_controller.py:587-591` — `recover_interrupted` calls `recover_agent_runs()` and
  `recover_verification_runs()` with no `workflow_id` while scoping only `recover_tasks`.
  Both defaults are `None` = all rows (`state.py:1139,1491`) and both transition runs to
  a terminal state. The engine gets this right at `workflow.py:239-243`. Latent only
  because nothing calls it and no test covers it — which is why to fix it before anything
  wires it up. `README.md:95` already claims recovery runs "also via the GUI".
- `git.py:77-78` decodes subprocess output with the console code page and catches only
  `(OSError, subprocess.TimeoutExpired)` at `:79-80`, so a non-ASCII repo path or branch
  name escapes as a raw `UnicodeDecodeError` from every method. The package already
  established the opposite convention at `registry.py:44-50`
  (`encoding="utf-8", errors="replace"`, with a comment naming precisely this failure).
  Same gap at `agent_run.py:219-227,240-248`, contained by `runner.py:220-224` at the cost
  of silently dropped `files_changed`/`diff_stat`.
- `scripts/build_windows_exe.py:1` claims "reproducible" and `README.md:52` says "Build a
  reproducible executable", but the script only runs PyInstaller and checks the `.exe`
  exists (`:24-27`). The archive-inspection and smoke-test procedure that `AGENTS.md`
  requires is manual and unenforced. `AgentOps.spec:29` sets `upx=True` with no pinned UPX;
  PyInstaller silently skips it when absent, so two builders produce different bytes.
- Leaf-module violations: `execution_model.py:3` declares itself a leaf ("no I/O, no
  SQLite, no LLM") but `:59` imports from `agent_run.py`, which imports `subprocess`
  (`agent_run.py:7`) and a private helper from `git` (`:13`). `persistence.py:27-28`
  declares itself a leaf but imports `redact_text` from `logging`, a filesystem writer.
  `agent_run.py:13` also reaches into `git._no_window_kwargs`, bypassing the sanctioned
  policy at `runtime.py:101-115` that `AGENTS.md` points every other module at.
- `verification_kernel.py:49-70` — `_summarize_counts` counts `CANCELLED` in
  `required_failures` but not in `failed`, so `total != passed + failed + skipped` for a
  cancelled run and `cli.py:234-236` prints totals that do not reconcile.
- `cli.py:83-95` — `_print_text` falls back to `sys.stdout.buffer`, which does not exist
  under captured or replaced stdout. Guard with `getattr(sys.stdout, "buffer", None)`.
- `agents/agents.yaml` lists `claude` in all four `role_preferences` arrays while
  `"enabled": false`; `registry.select` skips it, so the lists mislead maintainers.
- Contract drift in `agentops/AGENTS.md`: the test-module count is off (lanes variously
  read 21, 22, and 23 — drop the count, as the file already does for test totals), and
  the architecture map omits `agentops/persistence.py` and `agentops/agent_run.py`.

### universal-game-agent

- Make `main.py` refuse `type: external` with an error naming the sanctioned command.
  `training/experiment.py:44-47` delegates `type: external` to the external factory, so
  `train`/`evaluate`/`compare`/`experiment` all accept external configs and `README.md:182`
  documents exactly that — while `universal-game-agent/AGENTS.md` says "Do not route
  external runs through `main.py train`". The trap is that the toy driver has none of
  `external_experiment.py`'s machinery (no `launch_game`, `check_alive`, relaunch, or
  baseline-vs-trained comparison), so it produces a quietly weaker report under a command
  the docs bless. Delete the README sentence too.
- Report uncertainty in the comparison, not a sign: `main.py:306-313` should emit a paired
  difference with an interval, or say nothing. With `std_reward ~= 0.36` at `n=100`
  (effectively `n~=17`), the current `+0.04` is well inside noise.
- Give the external env a real `reset(seed=...)` via `super().reset(seed=seed)` plus a
  documented re-seed policy for the spawned game process, or stop recording `seeds`. A
  recorded-but-ignored seed is worse than an absent one.
- A failed external run produces no evidence at all: `train_with_window_relaunch` gives up
  by raising at `external_experiment.py:77-79`, so the report at `:305-338` is never
  written — after an hours-long run you keep `ppo_interrupted.pt` in a PID-scoped
  directory and no JSON pointing at it. Also `launch_phase2` (`:267-271`) calls
  `launch_game` then `wait_attach` outside any `try`, so an attach timeout leaks both the
  game process and the `log_file` handle (`:106,115`).
- Validate `checkpoint_every_updates` in `PPOConfig.__post_init__` (`training/ppo.py:42-55`
  validates every other field). `0` survives config load and dies at `:376`
  (`self.num_updates % 0`) after the first update — potentially hours in, on the slowest
  path in the repo.
- `capture.out_width`/`out_height` are validated, recorded, and then ignored in both live
  modes: `environment/external_game.py:380-382` reads and range-checks them, passes them
  only in the `synthetic` branch (`:414`), and omits them for `region` (`:421-422`) and
  `window` (`:430`). Native resolution is intentional (`:428-429`, and
  `extern_pong_rewards.py:16-18` depends on it), so the behavior is right and the key is
  the problem — both tracked results record `out_width=96, out_height=96` as though applied.
- Result metadata cannot reproduce the run: `external_experiment.py:310` records the raw
  `ppo.checkpoint_dir = checkpoints/extern_pong_02` while `:332` reports
  `checkpoints\extern_pong_02\run-15516\ppo_final.pt`; `:311-312` record the PID-suffixed
  title rather than the config's.
- Delete the dead layer: the duplicate `out_path` assignment at
  `external_experiment.py:297` and `:336`; `training/reward_diagnostic.py:33-39` defines
  `classify()` that `diagnose()` reimplements inline; `training/logger.py` and the
  `logging:` block in `configs/default.yaml` are wired to nothing outside tests.
- Refresh `README.md:101-108` (omits both shipped external configs), `:122-124` ("no
  external-game training loop" under *not implemented*), and `:174-178` (understates
  `reward`/`termination` as `null` and `never|step_limit` when the factory accepts
  `composite` and `extern_pong` at `external_game.py:399-405`).

### Instruction layer

- Scope `tasks/after-task.md` sections 1-3 to `agentops` and replace its baseline with a
  pointer to the two-row command table in `.agents/AGENTS.md`.
- Delete `lessons.md:13-15` (a false "greenfield / no code as of 2026-09-12" block sitting
  directly above 30+ product entries) and `:378` (false "no stack/test runner configured
  yet"). The file also runs 09-14, 09-13, 09-12, then restarts at 09-14 and continues to
  09-26 — ordering is append-by-arrival, not chronological, and should say so.
- Correct `project.md:33` and `roadmap.md:22` to the 2026-09-26 collaboration policy and
  land the pending `after-task.md:39-46` change in the same decision-logged commit.
  Leave `decisions.md:212-217` as the history.
- Replace `architecture.md:80-81` with a pointer to `team.md`'s liveness rule; delete the
  dead session ids.
- Retitle `audit.md` as "Historical audit (v0.1.1, 2026-09-13)" and drop it from
  `team.md:40`'s read list.
- Fix the fabricated `.gitignore` description in `project.md:131` and
  `opencode/sessions/2026-09-26-repo-reorganization.md:55`. Both describe an
  "ignore-all-then-allow pattern for `README.md` and `.gitkeep`" that
  `universal-game-agent/.gitignore:8-11` does not implement. The substantive claim is true
  (`git check-ignore` confirms it works), but a future agent reasoning from the stated
  mechanism will predict behavior the file does not have.
- Delete `.agents/memory/oh-my-pi/ohmypimemory.md` — a superseded duplicate that records
  HEAD `3adda60`, "246/246 green", "19 test files" (now 20 modules / 273 tests), and at
  `:40` asserts "External experiment NEVER completed. No external results JSON exists",
  contradicted by the tracked `exp_external_pong_02-15516_results.json` at commit
  `d15c02b`.
- Name the pre-approved temp directory once in `.agents/AGENTS.md` and use it everywhere.
  The collaboration protocol mandates POSIX `/tmp/agentops-review-*` and
  `/tmp/agentops-backup-*` in 7 places, while `opencode/environment.md:9` documents the
  shell as Windows PowerShell 5.1 and `after-task.md:61` itself hedges "(From git-bash...)".
  Every real backup recorded in memory used
  `C:\Users\admin\AppData\Local\Temp\...`.

### Repository hygiene

- Add `.gitattributes` (all 7 dirty files emit `LF will be replaced by CRLF`; no
  `.gitattributes` and no `.editorconfig` exist across 99 `.py` + 71 `.md` files) and
  `.editorconfig`.
- Broaden the checkpoint ignore. `universal-game-agent/.gitignore:9-11` covers only `.pt`,
  `.zip`, `.pkl`. Torch also writes `.pth`, `.safetensors`, `.ckpt`, `.bin`, `.npz`. One
  format change leaks 12 MB per checkpoint into a directory the product's own AGENTS.md
  calls "gitignored on purpose". 43 MB of weights are currently on disk.
- Make `logs/*.log` recursive. `universal-game-agent/.gitignore:14` is depth-1 only, and
  `training/external_experiment.py:50-57` opens a per-run log file — a run-subdirectory
  change silently starts tracking logs.
- Note that `!experiments/.gitkeep` (`:13`) is a no-op: `experiments/*/` matches only
  subdirectories, so the file was never ignored. Harmless, but it encodes a
  misunderstanding.
- Add a `pyproject.toml` for UGA and pin its dependencies. `requirements.txt` has 5 deps
  (`torch`, `gymnasium`, `numpy`, `pyyaml`, `mss`) with zero version specifiers, no
  lockfile, and a Python version stated only in a comment. UGA has nowhere to put
  packaging metadata. `agentops/pyproject.toml:9` is the only machine-readable
  `requires-python` in the repo.
- Add `agentops/tests/__init__.py`. UGA's `tests/` has one; agentops' does not, so the
  documented `python -m unittest tests.test_workflow`
  (`.github/copilot-instructions.md:43-47`) fails.
- Add `py.typed` to agentops despite 10,358 lines of package code and typed DTOs
  throughout.
- Fix the mojibake em-dash in `universal-game-agent/requirements.txt:1`
  (`# Universal Game Agent ─�? runtime dependencies`) — evidence the file was written or
  converted with the wrong codec.
- Add `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`/`htmlcov/`, `*.db`,
  `*.log`, and editor cruft to both existing `.gitignore` files.
- Stop documenting ephemeral Intercom names in memory at all — see Add item 13.

---

## Delete / archive candidates

| Target | Reason |
|---|---|
| `orchestrator.py` (root, untracked) | Dead prototype. Hardcodes `D:/admin/code/projects/.agents/orchestrator` (`:13`, an empty untracked dir), declares Python 3.12+ against the repo's 3.11 floor (`:2`), dispatches omp/pi/opencode with its own hardcoded table (`:21-27`) that `agentops/agents/agents.yaml` + `registry.py` + `routing.py` already own, uses `success = result.returncode == 0` (`:90`) — process exit code as verification, precisely what the success-evidence rule forbids — and rewrites task files with no single-writer coordination (`:62-74`) while writing into tracked `.agents/outputs` and `.agents/memory` (`:100,117`). In no ignore list, referenced by no instruction file. |
| `skills-lock.json` (root, untracked) | 4 of 7 `skillPath` values dangle (`skills/engineering/code-review/…`, `skills/engineering/diagnosing-bugs/…`, `skills/productivity/grill-me/…`, `skills/productivity/writing-for-agents/…`) because the upstream subdirectory layout was flattened in `65ef097` and no root `skills/` exists. All 7 `computedHash` values mismatch the SHA-256 of the corresponding `SKILL.md`. It covers 7 of 13 tracked skill directories and omits all six `antislop*` skills — the ones `AGENTS.md:18-28` and `.agents/AGENTS.md:90-104` actually mandate. It is the lockfile for the junctions in `.pi/skills/*` -> `.agents/skills/*`, so it belongs at `.pi/skills-lock.json`, inside the already-excluded `.pi/`. |
| `.agents/memory/oh-my-pi/ohmypimemory.md` | Superseded duplicate asserting "External experiment NEVER completed"; duplicates `oh-my-pi/architecture.md:17-34` with older content. |
| `.agents/memory/audit.md` | v0.1.1, 14 modules, 79 tests; omits 9 real modules; documents a since-fixed defect as live. Retitle as historical or delete. |
| `.agents/plans/chatgpt_recommendations.md` (840 lines) and `chatgpt_addition_recommendations.md` (1,194 lines) | 2,034 lines = 26.5% of all tracked markdown. The only two plans with no matching file in `.agents/outputs/`. `roadmap.md:273` itself calls them "advisory, not user-approved directives" and their content is already mapped into Track C. |
| `.agents/additions.md` | Duplicates `pending_tasks.md:5-30`, names `durable-json.windows-eperm.patch` which does not exist (the real files are `.opencode.patch` and `.pi.patch`), and gives root paths for files that live in `.agents/`. |
| `.agents/outputs/phase-2-verification-kernel.md` | Orphan output, no matching plan, zero references anywhere in the meta layer. |
| `lessons.md:5-11` | The 2026-09-14 Agent Intercom `writeDurableJson` entry concerns two external npm packages, not this repo. |
| `lessons.md:13-15` and `:378` | The false "greenfield / no code" block and the false "no test runner" line. |
| `small-projects/universal-game-agent/`, `small-projects/quickscripts/`, `.agents/orchestrator/` | All verified empty, all protected by "leave alone" rules (`oh-my-pi/project.md:9`, `ohmypimemory.md:6`, `oh-my-pi/lessons.md:12`) that therefore protect nothing. |
| `tasks/task-ignorethis.md` | Externally-authored Failure Analysis spec (author Hacir Bacoj, created `58355c5` 2026-09-15, last modified `4d408ed` 2026-09-16 "chore: sync externally managed task files and repository guidance"), tracked and clean, no secrets — but sitting in the canonical task namespace that `after-task.md:84-87` describes, with no product attribution, no author, and no "superseded" marker. Add a one-line provenance header or move it to `.agents/plans/`. |

### Keep but commit

- `universal-game-agent/experiments/exp_external_pong_01-14676_results.json` (7,516 bytes)
  — completes an otherwise complete set of 5 tracked result files, and the product's
  AGENTS.md states results are tracked evidence. Caveat: it was written by an older code
  version (it has `final_eval_mean_reward`, which HEAD never writes, and no `comparison`
  block), so it is not comparable to the tracked file. And per finding 1 it shows the same
  phantom-episode contamination.
- `agent-intercom-fix/` — 6 files / 22,172 bytes of upstream patch artifacts for two
  external npm packages, presented as live work by `pending_tasks.md:11-29` and
  `additions.md:9-20`, but excluded via `.git/info/exclude:10`. It has a real upstream-PR
  purpose and currently exists on exactly one machine. Commit it or drop the `.agents/`
  references to it.

---

## Verified genuinely good

Worth recording, because it is most of the code.

### agentops

- **Verification kernel** (`verification_kernel.py:171-189,227-267,269-336`) — empty suites
  and duplicate check names are rejected *before* any persistence write; fail-fast and
  continue-on-failure both behave; parallel groups cancel siblings only on terminal
  failure; external cancellation closes out every unfinished check and persists a CANCELLED
  report before re-raising; any lost `MUST_FAIL_CLOSED` write demotes a PASSED report to
  FAILED with the reason appended to the transcript (`:299-313`). The strongest module in
  the package and the model the other I/O paths should follow.
- **Process runtime** (`runtime.py:60-115,150-224,226-294`) — one spawn policy across
  platforms, a case-folded reduced environment with the `%SystemRoot%` fallback Bun-based
  CLIs require, cooperative cancellation on 50ms polling, process-group termination with a
  bounded 2x cleanup wait, and a `finally` that kills the child on external
  `CancelledError`. No orphaning path found.
- **Success-model invariants** (`execution_model.py:143-217`) — the four assertions are
  pure, total over well-typed input, and fail loud rather than coerce. `state.py` calls
  `assert_no_fabricated_success` from all three recovery paths (`:1183,1561,1926`), so
  `recover_*` cannot mint a success status without evidence.
- **Artifact store** (`artifacts.py:108-133`) — rejects both `../` traversal and symlink
  escapes via `resolve()` + `relative_to`; `:135-203` writes through a staging file and
  `os.replace` so a crash never leaves a partial artifact; `:205-217` verifies sha256 on
  read; 0700/0600 permissions applied; the crash-window orphan case is documented *and*
  detectable via `find_orphans` rather than glossed over.
- **Command safety and prompt hygiene** — no shell anywhere: commands are argument arrays
  validated by `config.py:80-83` and spawned via `create_subprocess_exec`. `runner.py:214-215`
  decodes child output as UTF-8 with replacement. `agent_run.py:50-95` persists only a
  prompt sha256 and length with `preview: None` and an explicit comment explaining why.
  `safe_command` replaces the prompt inside argv before command metadata is stored.
- **Recovery idempotency** — `workflow.py:257-266` uses the `(task, recovery_state)` pair
  as an idempotency key so a repeated recovery pass does not mint duplicate `Failure` rows,
  and `workflow.py:239-243` scopes all three recovery passes to the requested workflow.

### universal-game-agent

- **Layering genuinely holds** under a static import scan. No module under `interface/`
  imports `environment`, `training`, or `agent`; `environment/` touches `interface` only at
  function scope; `games/` never imports the agent. `interface/__init__.py` and
  `agent/__init__.py` both use lazy `__getattr__`, so torch stays out of the toy import
  path.
- **Recurrent PPO boundary semantics are correct and consistent between rollout and
  replay.** `compute_gae` (`ppo.py:68-82`) masks on `terminated` rather than `done` so
  truncation still bootstraps; `next_value` bootstraps `V(final pre-reset obs, its hidden
  state)` on truncation (`:206-209`); the update loop re-splits the minibatch at every
  `done` index and re-zeros the hidden state (`:244-258`) exactly as the rollout did. The
  done-boundary case for a `--resume` trainer is also handled (`:125-127`).
- **Timeout/termination precedence is right.** `external_game.py:303-304` forces
  env-level `max_episode_steps`/`max_episode_seconds` into `truncated=True` and never
  `terminated`, while a genuine banner termination still reports `terminated=True` — and
  `max_episode_seconds` correctly uses the injected `Clock` (`:312`), not wall time.
- **The `interface/` layer is well tested without a live window.** All 30 tests in
  `tests/test_interface.py` run against `FakeWindow` / `SyntheticBackend` /
  `RecordingBackend` and cover the failure-prone parts: key release on mid-hold exception
  (`:60-73`), chord press/release ordering and all-keys release (`:237-247`), cooldown
  throttling (`:249-255`), and the full `ActionDef` validation surface (`:264-282`). No
  test in the suite requires a real window or real input.
- **Curiosity does not leak across episode boundaries.** `ppo.py:282` passes `~buf["dones"]`
  as the validity mask, so transitions into a reset are excluded from both the predictor
  loss and the intrinsic reward, and `curiosity.py:139` guards the `RunningMeanStd` update
  against an all-invalid batch.
- **Zero `TODO`/`FIXME`/`HACK`/`XXX`/`NotImplemented`** markers anywhere in the tree.

### Repository and process layer

- **Zero tracked artifacts.** An exhaustive `git ls-files` scan for `__pycache__/`,
  `*.py[co]`, `dist/`, `build/`, `*.egg-info/`, caches, `logs/`, `*.log`, `*.db`,
  `*.sqlite*`, `node_modules/`, coverage files, and editor backups returns 0 hits. No
  `.gitignore` is broken; nothing was force-added. ~59 MB sits untracked on disk and 0
  bytes of it is in git.
- **No file is too large for git.** The largest tracked file is
  `agentops/agentops/state.py` at 90,165 bytes. No file exceeds 100 KB.
- **Append-only history is genuinely preserved.** `decisions.md` holds 30 dated entries in
  strict chronological order with nothing rewritten or removed; the two superseded
  collaboration decisions are preserved in place and explicitly superseded at `:212-217`.
  `lessons.md` is likewise append-only across 30+ entries.
- **Both headline rules sit at every point of action.** "Do not persist raw prompts,
  secrets, credentials, tokens, private keys" appears in root `AGENTS.md:15`,
  `.agents/AGENTS.md:64`, `pi_AGENTS.md:33`, `ohmypiagents.md:45`, `agentops/AGENTS.md:61`,
  `universal-game-agent/AGENTS.md:130`, and `.github/copilot-instructions.md:110`. "Agent-reported
  success is never verification evidence" appears in `.agents/AGENTS.md:69`,
  `pi_AGENTS.md:32,65`, `ohmypiagents.md:44,77`, `agentops/AGENTS.md:98`,
  `universal-game-agent/AGENTS.md:128`, and `.github/copilot-instructions.md:112`.
- **The shims behave as shims.** Root `AGENTS.md` (28 lines) and `pi_AGENTS.md` (14 lines)
  both redirect to `.agents/` and carry no independent content; the antislop block is
  identical across both shims and `.agents/AGENTS.md:90-104`.
- **Both product `AGENTS.md` files carry a "Known defects" section with file:line** — the
  highest-value docs in the repo, and the reason several findings above were findable at all.
- **No encoding corruption.** 0 of 182 tracked text files contain a U+FFFD replacement
  character. The `─�?` sequences seen while reading excerpts are PowerShell 5.1 console
  rendering (ANSI decode of UTF-8), not file damage.
- **Product-local instruction files are accurate where an agent would act on it.**
  `runtime.py:102-112` is the real `spawn_options()` with `CREATE_NO_WINDOW` at 109-111;
  `state.py:1615-1631` is the real `_migrate_failure_evidence()` with the v7
  `structured_evidence` column; `config.py:62` is the escaping `DEFAULT_CONFIG_PATH`
  recorded in Known defects; UGA's 20 test modules matches; `git check-ignore` confirms all
  five `.pt` files stay untracked while `README.md`/`.gitkeep` remain tracked, so the
  checkpoint-ignore fix from `65ef097` works.

---

## Confidence and caveats

- One lane's earlier report was partly retracted by its own errata before the corrected
  version arrived. The surviving `universal-game-agent/AGENTS.md` anchor defects are real
  but the line numbers were re-derived; spot-check before acting.
- Where lanes disagreed, treat the specific number as unverified and the defect as
  confirmed: `skills-lock.json` (0/7 vs 4/7 dangling paths) and the agentops test-module
  count (21 vs 22 vs 23).
- The meta-layer lane flagged that its own line anchors beyond a certain depth came from
  batched reads with truncated per-file output. The line *content* is high-confidence; the
  exact line numbers for memory-file citations in findings 9-17 are not individually
  re-verified. The anchors in findings 1, 2, 3, 18, 19, 21, and 23 were re-read and
  confirmed.
- Neither test suite was executed. Any claim about current test counts or pass/fail state
  is unverified.
