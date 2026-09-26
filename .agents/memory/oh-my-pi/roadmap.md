# universal-game-agent — Roadmap

## Done (all verified green at commit time)
- [x] Scaffold, config system, logging, README baseline
- [x] Pixel-only Toy Pong + preprocessing (gray/84/stacked/skip)
- [x] CNN+GRU actor-critic + recurrent PPO + timeout-aware GAE
- [x] Curiosity (ICM forward-dynamics) + toy A/B evidence
- [x] Evaluation (greedy/sampled, mode restore, action histograms)
- [x] Checkpoints + resume (model/optim/counters/curiosity/env_config)
- [x] Thin CLI (smoke-test/train/evaluate/experiment)
- [x] Windows interface (capture/controller/adapter) + try/finally holds
- [x] ExternalGameEnv + lifecycle/reward/termination contracts + timing
- [x] Composable + config-driven rewards with diagnostics
- [x] Standalone extern Pong + OS-loop smoke + screen reward/termination
- [x] External experiment runner (3-phase, isolated, logged)
- [x] Docs/config sync; two full audit passes (PPO/GAE, general)
- [x] Extern-Pong reward/termination fix (b77bf4d): native-frame detection,
  latched once-only edges; live-verified Diag320 11/11 + Diag96 6/6 terminal.

## Next (proposed, in order)
- [x] Complete one full external 3-phase run with results JSON (exp02: 4096 steps,
  exit 0, comparison block verified; verdict no-learning, see project.md).
- [x] STEP 1 — diagnose 3-step episode deaths: CAUSE = reset() lands mid-banner
  (1.0s persistence) -> 1-step phantom terminations; live rally dies honestly.
- [x] STEP 2 — reset() polls for a non-banner frame (reset_settle_timeout_s,
  default 5.0s; screen-only; best-effort). Live: 0/6 phantoms. Suite 273 OK.
- [ ] STEP 3 — pre-flight: check live-play baseline reds in the exp window
  (59-red chrome seen in verify window could mask hit edges); then re-run exp02
  config (4096 first; 30k once stable) with the same untrained/trained/delta
  comparison. Judge by miss-rate + episode length.
- [ ] Reliability: window-death mid-training ("target window is gone", empty game
  log, killed a 30k run at step 6144). Consider relaunch-and-resume or
  checkpoint-every-N-updates so partial runs stay usable.
- [ ] Curiosity enabled on the external game (config flag exists).
- [ ] Commit pending work (`universal-game-agent/` + `.agents/memory/oh-my-pi/` only).

## Explicitly not planned
- LSTM/Transformer swap, SB3 adoption, continuous-action PPO, model-size scaling
  (all rejected for lack of evidence; see decisions.md).
- Live SendInput end-to-end tests (side effects); multi-seed statistics (cost).
- Client-area-only capture (full-frame documented as intended).
