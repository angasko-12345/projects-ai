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
