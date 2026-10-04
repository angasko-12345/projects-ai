# universal-game-agent — Roadmap

> Superseded for status purposes 2026-10-02. This is a UGA-specific historical roadmap.
> The live task queue is `.agents/pending_tasks.md`; the live bug ledger is
> `.agents/memory/opencode/bugfinding/master-bug-synthesis.md` §0. Suite counts below are
> dated observations; the current baseline (2026-10-03, after the external-orchestration
> batch) is 349 tests, 1 skip, OK (the 318 after `59f5a1b` was superseded later the same day).

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

## Done 2026-10-01 (commit 2cb2413, suite 288/288 — superseded, current is 318/1 as of 2026-10-03)

- [x] P5 checkpoint safety: interval validation (0 = disabled), atomic
  temp-file replace, 4 regression tests.
- [x] P6 lifecycle: launch-failure ownership, temp-env close, idempotent
  stop, eval-env close on exception, 6 regression tests.
- [x] P7 contracts: seed-no-op + hits/misses semantics documented, 1 test.
  No reset-behavior change, no results invalidated.
- [x] P8 CI: per-product workflows + scope doc; three-product routing.
- [x] P9 freeze: A5/A6/A7 deferred (`.agents/plans/architecture-freeze-a5-a6-a7.md`).
- [x] P10 mini-llm: TinyStories experiment record + verified loop.

## Next (proposed, in order)

- [ ] STEP 3 pre-flight + exp02 re-run (unchanged top item).
- [ ] Watch: `data/tokenizer.json` deletion — if the user restores it, the
  3 `TestGenerationSeed` errors should clear with no code change.
- [ ] mini-llm scale: full 1.9 GB TinyStories prep + GPU run (procedure in
  EXPERIMENT doc; needs cloud GPU, not this box).
