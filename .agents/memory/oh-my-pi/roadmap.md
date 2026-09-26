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
- [ ] Complete one full external 3-phase run with results JSON (~6 min free desktop).
      Requires: nothing new — `training.external_experiment` is ready.
- [ ] Commit pending work (see git status; `universal-game-agent/` only).
- [ ] Make the external task learnable WITHOUT touching model/algorithm:
      faster ball / narrower paddle / off-center serves in `games/`,
      and/or a small per-step survival penalty via the existing composite
      reward config. Re-run A/B; judge by eval miss-rate, not vibes.
- [ ] Longer external training budgets once the task discriminates skill.
- [ ] Curiosity enabled on the external game (config flag exists).

## Explicitly not planned
- LSTM/Transformer swap, SB3 adoption, continuous-action PPO, model-size scaling
  (all rejected for lack of evidence; see decisions.md).
- Live SendInput end-to-end tests (side effects); multi-seed statistics (cost).
- Client-area-only capture (full-frame documented as intended).
