# universal-game-agent — Lessons (durable, reusable)

## Tooling / workflow
- Re-ground edit anchors on every call; stale anchors scramble files. After any
  multi-edit batch: syntax check + targeted tests before proceeding. (Repaired
  `ppo.py`, `experiment.py`, `main.py`, `evaluate.py`, tests repeatedly.)
- `python -m pkg.mod` warns if package `__init__` eagerly imports the submodule;
  use lazy `__getattr__` re-exports.
- Windows `FileHandler` locks block `TemporaryDirectory` cleanup — tests must
  close/remove handlers in `finally`.
- YAML edits: verify key uniqueness after line-targeted edits.
- Commit `universal-game-agent/` + `.agents/memory/oh-my-pi/` only; other
  `.agents/` peers, `tasks/`, `small-projects/`, root strays change under
  us — leave them alone.
- Full suite (`python -m unittest discover -s tests`, ~25 s) after every change set;
  then remove `__pycache__`. Suite must leave `checkpoints/` with only `.gitkeep`.

## Testing
- Torch imports in tests need skip guards for torch-less interpreters.
- Fakes only: Recording/Synthetic/FakeClock/scripted envs. No display, input, or network.
- Zero-bias GRU + zero input = exactly-dead forward pass (zero values AND zero critic
  grads); test fakes must use nonzero frames.
- Hand-verify test arithmetic independently (λ=1 GAE accumulation and unbiased-std
  normalization both burned us once); component-level probes beat reasoning.

## Windows OS interaction
- `taskkill /PID x /F` works; PowerShell `kill` without `-Force` silently fails;
  `wmic` absent (use `Get-CimInstance`).
- Always follow kills with a SendInput `key_up` sweep (arrows/WASD/space/modifiers/mouse):
  killing mid-hold strands keys system-wide.
- Suspected stuck key? Sample `GetAsyncKeyState` over ~2 s first: constant-True =
  stuck; toggling = live holds. Untrained greedy argmax is often constant — check data
  (`action_counts`), not eyeballs.
- Pair game windows to owner PIDs via `GetWindowThreadProcessId`, not process lists.
- `tasklist /FI "WINDOWTITLE eq X"` matching is unreliable; enumerate with ctypes.
- Verify singularity by titled-window enumeration, not process count alone.

## Debugging live runs
- Per-phase game stdout logs + post-phase liveness asserts turn silent window death
  into diagnosable failures.
- Calibrate pixel thresholds against live frames (ball 19–36 px, banner 619 px here),
  keep order-of-magnitude margins, keep thresholds as constructor params.
- Window-border pixels contaminate naive centroid measurements; restrict to known bands.

## 2026-09-26: reward-fix session
- Never retune thresholds to compensate a resize: count events on the native frame,
  resize only the network observation. Diagnostic trick that proved it: print the
  downscaled count as a reference column without using it (banner: 607 native vs
  61 @96 — inside the 8–200 hit band).
- Toggle-edge rewards double-pay on held states; latch the signature and pay only on
  entry transitions (falling hit edge pays 0, held banner pays 0, reset pays 0).
- Edit-tool gap PUTs can silently drop neighboring lines (lost the diagnostic's label
  computation and half of `WindowCapture.__init__`); re-read the touched region after
  every edit and treat tool-echo content as unverified until a fresh read confirms it.
- Background `bash` services run in WSL (/bin/bash, cwd /mnt/d/...): `D:/` and `/d/`
  paths both fail there — use `/mnt/d/...` for the venv python and repo paths.
  Foreground calls accept `D:/...`. Exit 127 = path convention, not a real failure.
- Line-targeted yaml edits eat mapping headers (`ppo:` vanished, PPO keys merged
  under `model:` -> `error: 'ppo'`); always re-parse the config after editing it.
- `pkill -f <pattern>` matches its own command line: the shell SIGTERMs itself
  (exit 15, no output). Use a self-excluding pattern (`pkill -f "Trace[D]eaths"`).

## 2026-10-01: P5–P10 session (omp)
- Edit-tool range discipline: `PUT N.=M` REPLACES the whole range — including
  it dropped the seed/gamma checks from `PPOConfig.__post_init__` once (caught
  on re-read, restored). Wide PUTs over validation blocks are the danger zone;
  re-read the touched region after every edit, never trust the echo.
- `PUT` body `++` is a literal `+` line (syntax error), not "add blank". To
  delete one line use `CUT N.=N`. To insert use `PUT >N:`.
- `bool` is an `int` subclass: `isinstance(True, int)` passes. Config
  validation that accepts ints must exclude bools explicitly where `True`
  as `1` is nonsense (did so for `checkpoint_every_updates`, `seed`).
- Windows python does NOT see bash `/tmp/...` paths (resolves to a
  nonexistent drive-relative path; `tokenizers` save died with os error 3).
  Use `$TEMP`-rooted native paths (`$TEMP/mlsmoke`) for throwaway artifacts.
- `train.py` has no `--tokenizer-path`/`--eval-interval` flags; tokenizer
  path for generation comes from the checkpoint config (or explicit
  `--tokenizer`); eval cadence is config `eval_interval` (default 500).
  Check `--help` before scripting smoke runs.
- Reported-but-absent bugs: `launch_phase2` leak and phase-3 env leak were
  REAL on current source (fixed); phantom-reset and GAE truncations were
  already-fixed (settle + `terminated`-masking, tested) — changed nothing
  there. Reproduce-or-disprove each lead individually; never batch-apply old
  audit fixes.
- Nested helpers (`launch_phase2` inside `run_external_experiment`) are
  untestable — extract module-level `launch_phase2_process` /
  `load_eval_model` and test those with stub procs/envs. `_FakeProc`
  pattern (terminate/kill/wait counters + `_log_file`) lives in
  `tests/test_external_experiment.py`.
- mini-llm tiny-sample smoke recipe (all outputs outside repo):
  `prepare_data.py --input data/raw/train.txt --tokenizer-out $T/tok.json
  --train-out $T/train.bin --val-out $T/val.bin --vocab-size 64
  --min-frequency 1 --context-length 16` → `src.train` 6 steps →
  `--resume` 2 more → `generate.py --tokenizer $T/tok.json`. Proves the loop
  without touching `data/` or `checkpoints/`.

## 2026-10-03: ROOT-034 DBG-06/07 — test-infra pass (omp)
- Four distinct reasons direct exec lied, only three audited: `sys.path[0]=tests/`
  import crash (12 files), `except ImportError → _HAS_TORCH=False` silent all-skip
  (8 files), mid-file `unittest.main()` hiding later classes (7 files), and — found
  only after fixing those — `test_compare_dispatch` leaking its `side_effect` mock
  into `training.experiment` via `cmd_compare`'s lazy import (discovery hides this:
  collection imports every module before any patch is active). Root cause of the
  mock leak class: patch targets are import-order-sensitive.
- Fix pattern now canonical for UGA tests: 4-line prelude
  (`try: from . import _bootstrap / except ImportError: import _bootstrap`) right
  after the docstring, `unittest.main()` only at EOF. New test files get both.
- Verification recipe that catches all four: run every `tests/test_*.py` from a
  fresh `mktemp -d`, print each file's `Ran N`, compare against discovery's
  per-module counts (equal → no hidden classes, no silent skips), then assert the
  scratch dir is empty (→ no CWD writes). Expected per-module counts are in the
  `discover -v` output.
- Checkpoint isolation: `_scratch_checkpoint_dir()` helper in `test_diagnostics`,
  inline `TemporaryDirectory` in `test_external_training`, and the guard
  `tests/test_cwd_isolation.py` runs the 4 offenders under a scratch CWD and
  asserts empty. The mtime of `checkpoints/ppo_final.pt` across a suite run is a
  one-line pollution probe.
- Baseline: 317 → 318, 1 skip, OK (commit `59f5a1b`). Pre-existing, NOT fixed:
  gitignored working-tree `checkpoints/ppo_final.pt` (1.49 MB) already clobbered
  by pre-fix runs — reported, not regenerated; `ppo_untrained.pt` intact.

## 2026-10-06: AI Token Tracker shot-3 — driving Qt GUIs through the computer prelude

- **The `computer` window API is async and frame-bound:** every `_Window` method
  (`screenshot`, `click`, `press`, `raise_`, `find`) must be awaited; `win.click(x, y)`
  accepts only pixels of the *most recent screenshot of that window* (out-of-frame
  coords raise `InvalidCoordinateFrame` naming the frame size), and `win.screenshot()`
  captures the window only — combo popups are separate HWNDs and never appear in it.
- **Coordinate clicks silently fail when the window lacks foreground:** with fullscreen
  Roblox and a second agent session contesting the desktop, in-frame clicks hit
  nothing while `raise_` failed with `InputFailed ... foreground lock`. AX element
  clicks work without focus: find an element carrying the right `title` and
  `await el.click()` (this toggled themes and switched tabs reliably).
- **`win.find` returns at most 100 elements** and the tab bar sits past the cap;
  walk instead: `kids = await el.children()` (it is an async *method* — `el.children()`,
  not the `el.children` property I first awaited), recursing to ~depth 8 found
  `tabgroup`/`tab`/`combobox` every time. `ax()` returns the full tree as a string
  with refs — good for locating, useless for acting.
- **Prove QSS widget rendering without winning the desktop fight:** a throwaway probe
  (`QApplication` + `build_stylesheet(dark/light)` + `combo.showPopup()` +
  `combo.view().grab() → PNG`, read both images) showed dark popup = light text on
  #161B22 with green selection, light popup = dark text on white — the exact
  regression the user reported, verified in both themes in seconds, no focus needed.
- **GUI verification hygiene:** enumerate `Get-CimInstance Win32_Process` for
  `*token_tracker*` before trusting what you see — a stale shot-2 instance was live
  next to mine (two same-titled windows); close with AX `Close` or
  `os.kill(pid, SIGTERM)` (`WinError 5` often means it is already exiting); verify
  theme persistence only by *relaunching* (QSettings is read in `main()`, so a live
  toggle proves nothing about restart); hunt "clipped ghost" header artifacts by
  cropping + 3× upscale before assuming a QSS bug — it was the intended
  `setSortIndicator` on the sorted Tokens column.

## 2026-10-07: subprocess GUI tests must skip on missing Tk, not just missing display
- "No display available" and "tkinter unavailable in this Python build" are two different environment limits producing the same exit-2 shape in `extern_pong --auto-quit`; the test now skips on either string. When adding a GUI-launch test, enumerate the missing-GUI exits (no X/Wayland, no Tk build, no OpenGL) up front instead of discovering them one CI host at a time.
