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
- [x] Honest eval reporting: real terminated/truncated counts + untrained/trained/
  delta comparison block, no auto-verdict (d15c02b). Suite 268 at the time.
- [x] exp02 validation run (4096 steps, exit 0) — verdict later suspended
  (phantom confound); results JSON kept as pre-fix artifact.
- [x] Phantom-reset diagnosis (Step 1) + reset-settle fix (Step 2, 6890c42):
  live 0/6 phantoms. Suite 273 at the time.
- [x] Phase-2 window-loss tolerance (Step 4, f751d73): relaunch-and-resume
  (max 3) + ckpts every 25 updates. Suite 278.

## Next (proposed, in order)
- [ ] STEP 3 — pre-flight: check live-play baseline reds in the exp window
  (59-red chrome seen in verify window could mask hit edges); then re-run exp02
  config (4096 first; 30k once stable) with the same untrained/trained/delta
  comparison. Judge by miss-rate + episode length.
- [ ] Curiosity enabled on the external game (config flag exists).
- [ ] Commit pending work (memory files only right now).

## Explicitly not planned
- LSTM/Transformer swap, SB3 adoption, continuous-action PPO, model-size scaling
  (all rejected for lack of evidence; see decisions.md).
- Live SendInput end-to-end tests (side effects); multi-seed statistics (cost).
- Client-area-only capture (full-frame documented as intended).
