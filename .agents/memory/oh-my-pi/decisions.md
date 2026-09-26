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
