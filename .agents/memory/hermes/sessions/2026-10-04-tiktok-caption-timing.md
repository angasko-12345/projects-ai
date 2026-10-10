# Session — 2026-10-04 — speech-aware caption timing (tiktok-slop-factory)

## What was asked

Replace uniform caption timing in `tiktok-slop-factory` with a deterministic,
dependency-free, speech-aware model. Scope was strictly `app/captions.py`, the
caption call site in `app/pipeline.py`, caption tests, and docs/memory after
verification. Gemini, TTS, renderer, visuals, output verification, batch logic,
CLI, and manifest/resume were explicitly out of scope.

## What shipped

Commit `58d50c2` — `feat(tiktok): add speech-aware caption timing`.

Five files: `app/captions.py`, `app/pipeline.py`,
`tests/test_script_captions.py`, `README.md`, and a 7-line entry in
`.agents/memory/decisions.md`.

The model weights each word by a base cost, syllables beyond the first, and
trailing punctuation, then distributes the **probed narration duration** in
proportion. Constants: `BASE_WORD_WEIGHT = 1.0`, `SYLLABLE_WEIGHT = 0.35`,
pause `,` 0.50 / `;` `:` 0.70 / `.` `?` `!` `…` 0.90, `MIN_CUE_SEC = 0.40`,
`MAX_CUE_SEC = 7.00`.

Full reasoning, rejected alternatives, and verification numbers are in
`.agents/memory/decisions.md` under the 2026-10-04 caption-timing entry. Read
that file rather than trusting this summary.

## Three decisions worth remembering

1. **The floor is per caption, not per word.** See `lessons.md` in this folder.
   The pipeline passes `max_words=3`; bounds on the intermediate word cue
   multiplied into a hard pipeline failure on a 32-word / 6s narration.
2. **Bisection, not clamp-and-rescale.** The clamped sum is monotone in the
   scale factor; the iterative loop diverges. Same lessons file.
3. **`MAX_CUE_SEC` can end a track early.** When narration outlasts
   `count × MAX_CUE_SEC`, every cue caps at 7s and the track stops before the
   audio does. This is intended (the alternative is one absurd caption) and is
   covered by `test_cues_end_before_narration_when_words_cannot_fill_it`, but it
   is a real behaviour change for sparse scripts with a slow TTS voice — watch
   for it if captions ever appear to end early in a real video.

## Two existing tests were changed on purpose

Not regressions, and worth knowing before someone "fixes" them back:

- `test_timestamps` asserted 3 words over 60s ends at 60s — a 20-second cue,
  exactly the absurd-cue case the bound forbids. Changed to 6s.
- `test_max_cue_cap_still_covers_the_whole_narration` now probes at exactly
  `count × MAX_CUE_SEC`, since a longer narration legitimately ends early.

## Verification

`python -m pytest tests -q` → **109 passed, 2 failed**. Both failures
(`test_loads_dotenv_from_project_root`,
`test_generate_ideas_rejects_duplicate_padding`) are environmental, need
`GEMINI_API_KEY`/`.env`, and were reproduced on the untouched tree before any
edit. Caption module alone: 32 passed. Baseline was 97 collected.

The task brief specified `python -m unittest discover -s tests`; that collects
0 tests on this project, prints `NO TESTS RAN`, and exits 5. It is pytest.

## Not done / not touched

`.agents/pending_tasks.md` was off-limits by instruction and was not modified.
No Gemini, TTS, renderer, visual, verification, batch, CLI, or manifest changes.
The two environment-dependent failures were left failing deliberately, as they
are unrelated to captions.