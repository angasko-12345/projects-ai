# universal-game-agent — Decisions (append-only; newest last)

## 2026-09-23: Custom single-env recurrent PPO, no SB3
- Rationale: full control of GRU hidden-state semantics across episode boundaries.
- Consequence: we own GAE/buffer/replay correctness (audited twice since).

## 2026-09-23: Hand-rolled numpy bilinear resize; no cv2/PIL
- Rationale: keep runtime deps minimal. Verified against uniform/spatial tests.

## 2026-09-23: Single-env PPO, no vectorization
- Rationale: correctness first; ~100 FPS on CPU is enough for current budgets.

## 2026-09-23: Removed stable-baselines3 (never imported)
- Rationale: dead dependency. Custom PPO is the only algorithm.

## 2026-09-24: Checkpoints use `weights_only=True`
- Rationale: safe loading; verified against all checkpoint shapes in-repo.

## 2026-09-24: Unknown config keys warn, never silently drop
- Applies to PPO/curiosity/env/reward factories. Forward compat preserved.

## 2026-09-24: Timeouts are truncations, never terminations
- Applies in `ExternalGameEnv`, GAE masking, diagnostics counting, replay segmentation.

## 2026-09-24: No press-only/release-only actions
- Rationale: down-without-up strands keys OS-wide (observed incident). Holds always release in `finally`.

## 2026-09-24: Duplicate-execution hardening over prevention
- Background launches were observed executing twice (identical timestamps). Instead of
  preventing it (platform behavior, out of our control): unique PID-suffixed window
  titles, PID-isolated checkpoint/result paths, stale-title preflight refusal.

## 2026-09-24: Greedy-eval action histograms recorded (`action_counts`)
- Rationale: "only pressing X" disputes become checkable data instead of eyeball judgments.

## 2026-09-25: No-learning verdict on external Pong stands as environment design
- Fresh 11.83 vs trained 10.83, both pinned at the 200-step cap: a static centered
  paddle rallies serves, so the task rewards survival, not skill. No model/algorithm
  change authorized on this evidence; task-side pressure (faster ball, survival penalty)
  is the recommended next lever.

## 2026-09-26: Native-frame reward/termination detection + latched once-only edges
- Rationale: thresholds were right, the frame was wrong — 607px banner collapses to
  61px at 96x96 (inside the 8–200 hit band). Retuning thresholds would chase the
  resize; detecting on the native frame removes the coupling entirely.
- Consequence: `ExternalGameEnv` carries both `raw_frame` (providers) and resized
  `frame` (network); captures default to native passthrough with resize only on the
  observation path. Toggle-edge rewards replaced by latched signature transitions
  (hit->hit, miss->miss, either->anything all pay 0). Falling hit edge no longer pays.

## 2026-09-26: Shortened exp02 to 4096 steps after 30k attempt died environmentally
- Rationale: window vanished at step 6144 ("target window is gone", empty game log);
  shorter run limits exposure while still testing the full report pipeline. Budget
  cut is runtime risk management, not a hyperparameter change.
- Consequence: exp_external_pong_02.yaml now 4096 steps / 100 eval eps; 30k stays
  open for when the task is discriminative and the window is stable.

## 2026-09-26: 2026-09-25 no-learning verdict SUPERSEDED (phantom confound)
- The 09-25 verdict ("task rewards survival, not skill") rested on pre-fix runs:
  the 96x96 MISS-as-hit bug inflated rewards and suppressed termination (episodes
  pinned at the 200-step cap), and exp02 later showed ~5/6 episodes were phantom
  resets. None of that evidence measures the task. The verdict is suspended, not
  inverted: no claim about learnability stands until a post-fix re-run (Step 3).
- Consequence: task-side levers (serve spread, ball speed, survival shaping) stay
  proposals, not conclusions. Judge the re-run by miss-rate + episode length.

## 2026-10-01: `checkpoint_every_updates=0` means disabled (P5)
- Rejected "reject all non-positive": three shipped experiment configs use
  1000-as-effectively-disabled, so disabled is a wanted semantic. Made it
  explicit (0) instead of keeping the silent never-fires drift.
- `final.pt` always written regardless of the interval.

## 2026-10-01: Cleanup ownership transfers only on success (P6)
- `launch_phase2_process` stops the proc inside on attach/liveness failure
  because the caller never receives the handle. `stop()` idempotent via
  `_stopped` so failure-path and session cleanups can overlap safely.
- `load_eval_model` owns the throwaway env (try/finally close) — the
  checkpoint object never takes env ownership.

## 2026-10-01: Measurement limits documented, not coded around (P7)
- External `reset(seed=)` stays a documented no-op (game seeded once at
  launch). Wiring per-episode reseeding through process restart would be a
  behavior change with live-timing consequences — explicitly out of scope.
- `hits`/`misses` keep counting reward sign (report schema frozen); the
  contract now states which providers they are meaningful for.

## 2026-10-01: No per-product test-command invention in CI (P8)
- Workflows run exactly the documented `python -m unittest discover -s tests`
  per product. No lint/format/coverage gates (none configured anywhere).

## 2026-10-01: TinyStories artifact accepted as baseline without re-running (P10)
- The 168 MB `checkpoints/tinystories/final.pt` was verified (config, losses,
  seeded generation sample) but NOT resumed — a resume would rewrite the
  artifact. Resume path proven on a `$TEMP` tiny loop + existing suite tests.

## 2026-10-03: Test-only bootstrap for direct execution; no production path code
- DBG-06: `tests/_bootstrap.py` (repo-root sys.path insert) + 4-line prelude in
  all 20 modules. `python -m unittest discover -s tests` stays the source of
  truth; direct `python tests/<file>.py` from any cwd is a convenience running
  the identical tests (per-module `Ran N` verified equal).
- Rejected: pytest/conftest (second mechanism + dependency), production
  sitecustomize/path hacks, rewriting all 20 files' imports. The one extra fix:
  `test_compare_dispatch` pre-imports `training.experiment` so its patch window
  cannot bind a mock into the module (import-order leak).
- DBG-07: the 4 checkpoint tests point `checkpoint_dir` at temp dirs and KEEP
  their `ppo_final.pt` asserts — location changed, claim strengthened. Production
  default `checkpoints` untouched. Guard: `tests/test_cwd_isolation.py`
  (scratch-CWD run + empty-dir assert).
