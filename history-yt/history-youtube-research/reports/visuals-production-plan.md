# Visuals Production Plan

How `histyt` should source and generate images so its videos look like the
history channels that actually win, without adopting their legal risk.

Written 2026-11-05. Every claim below is labelled the way the rest of this
research is: `VERIFIED` (checked in this repo or a primary source today),
`ESTIMATE` (arithmetic with the method shown),`HYPOTHESIS` (pattern worth testing, not measured).

**Status 2026-11-05:** slices 1-2 are implemented — `histyt/visual_spec.py`
builds each scene's type, queries and period constraints, and
`visuals-fetch --all` resolves one licensed Commons image per scene. A live run
on two Teutoburg scenes selected both, through a fallback query, at CC0 and
public domain. Yield is the open question: Commons search filters by license
after retrieval, and specific moments are thin. The Arminius painting for 9 CE is
1519px wide, under the default 1600px floor, which is why the Met/AIC provider
in slice 3 is the next thing that moves this.

---

## 0. Where the pipeline stands

| Part | State |
|---|---|
| Scene manifest with per-scene style lock, camera, lighting, transition, duration | built (`histyt/scenes.py`) `VERIFIED` |
| Visual stage: one deterministic gradient card per scene | built (`histyt/visuals.py`), placeholder by its own docstring `VERIFIED` |
| Commons fetch with license allowlist, content-type and size checks | built (`histyt/commons.py`), but takes one hand-typed `--query` and does not map results to scenes `VERIFIED` |
| Motion | zoompan with one camera move per segment (`histyt/assemble.py`) `VERIFIED` |
| AI image provider | interface stub only, `ai_image.provider: disabled` `VERIFIED` |
| Visual direction, style lock, prompt template, per-era accuracy notes | `handbooks/ai-visual-production-handbook.md` `VERIFIED` |
| Visual stack and budget recommendation | `reports/recommended-production-blueprint.md` `VERIFIED` |

**Honest limit:** `data/video_analysis.csv` records `visual_production_analysis`
as `UNKNOWN` for all 60 sampled videos. The metadata sample cannot see frames,
so the archetype mix in section 1 is direction from the handbook plus public
channel patterns, not a measurement. Frame-sampling three videos per archetype
is how we close that gap.

## 1. The visual mix to copy

Viral history channels are not one look. They are four archetypes, and the ones
that scale mix them deliberately instead of picking one.

| Archetype | Reference channel family `HYPOTHESIS` | Cost per finished minute | Legal risk | Trust | Share, 12-20 min doc | Share, 2 min v1 |
|---|---|---|---|---|---|---|
| Archival still or artwork, slow push | Fall of Civilizations family | low | low if PD | high | 40% | 50% |
| AI still, parallax / Ken Burns | History Dose family | medium | low, needs disclosure | medium | 40% | 30% |
| Map or battle diagram | Kings and Generals, BazBattles | high | low (self-made) | high | 15% | 10% |
| Typographic beat (number, quote, date) | most channels as punctuation | trivial | none | high | 5% | 10% |

Channel names are indicative, not evidence: we did not inspect their frames.
What transfers is the mix, the pacing, and the discipline of one look per video.
Do not copy a channel's style lock, thumbnail design or series name.

**Why this mix:** archival material carries credibility for free, generated
stills cover the beats that never had a camera, maps carry geography-led
military topics, and typographic beats give the edit rhythm between images.
`ESTIMATE`: a 30-scene documentary at these shares is 12 archival, 12 generated,
4 maps, 2 typographic.

## 2. The one rule that makes it history rather than slop

Every scene's image must depict that scene's period and place, or be explicitly
non-literal (a map, a diagram, a date, a quote card). A generic parchment or
"old paper" still standing in for a claim is the failure mode that reads as AI
slop to anyone who knows the period, and the QA rubric already scores it:
`visual quality` 1-3 is "artifacts; wrong scale", `historical authenticity` 1-3
is "anachronisms".

Consequence: cards stop being the default for release videos. They stay as a
debug fallback, and the QA gate fails a release build that ships them unless
`visuals.allow_placeholder_cards` is explicitly set.

## 3. Data model additions

Additive only; existing runs regenerate assets rather than migrate.

Per scene in `scene_manifest.json`:

```
visual_type        archive | generated | map | diagram | card
visual_query       the search string the scene resolved to
subject            what must be visible
period_note        the handbook's accuracy constraints for this scene
authenticity_risk  low | medium | high
asset_status       pending | selected | missing
```

Per asset in `assets.json`:

```
visual_type, provider, model, prompt, seed
width, height, phash, sha256
license, license_name, license_url, credit_line, source_url, query
```

`validated_asset()` keeps its current job (license allowlist) and gains the
resolution and attribution checks.

## 4. Tier A: archival sourcing (build this first)

Cheapest, fastest, and the only tier with no disclosure obligation.

Providers in order:

1. **Wikimedia Commons** — already wired. Broadest coverage of people, objects,
   buildings and old maps. License metadata is messy, which is why the allowlist
   and raw-license recording stay.
2. **Art Institute of Chicago API** and **The Met Collection API** — CC0,
   museum-quality paintings, prints and objects. Best source for "what it looked
   like" and material culture; both return clean JSON and high-resolution files.
3. **Library of Congress** (`loc.gov` JSON) — photographs, posters, maps for
   19th-20th century topics; US federal work is already on the allowlist.
4. **Europeana** — huge, but rights statements are heterogeneous. Accept only
   records whose statement maps onto the existing allowlist.

Deliberately **not** used: Getty, Alamy, IWM, most national archives with
"personal use" terms, and CC BY-NC sources such as David Rumsey maps. Monetised
video needs commercial-use rights or public domain, and the gate must say no
rather than hope.

Per-scene retrieval:

1. Build three queries from the scene: `subject + period`, `material culture +
   location`, and an era fallback. Strip narration wording; never search the
   sentence the narrator reads.
2. Score candidates on: license tier, resolution (≥1600×900), aspect after crop,
   phash distance ≥12 against already-selected assets and the last 10 videos,
   and keyword overlap with `subject`.
3. Pick deterministically (score, then sha1 of the URL) so a rerun selects the
   same file, and store its sha256.
4. No candidate after three queries means the next tier, never a wrong-period
   substitution.

Cost: zero fees; rate limits only. Cache downloads by URL hash under
`runs/<id>/images/cache/`.

## 5. Tier B: generated stills

Provider-agnostic, because the field moves monthly. `ai_image.provider` grows
into an interface with one implementation per route:

- **Local ComfyUI + FLUX** — the blueprint's pick. No per-image fee, GPU time
  instead, full control, slower to operate.
- **Hosted single-model** (fal FLUX class) — the blueprint's fallback for hero
  stills when local quality or speed falls short.
- **Multi-model aggregators** (WaveSpeed, Pixazo) or **ImagineArt** — one key
  across many models, useful for the bake-off. Per-image pricing and commercial
  licensing must be confirmed in writing before a monetised video uses them; the
  catalogue documents neither.

**Decision gate:** bake off 10 prompts × 3 routes, scored on period plausibility,
style consistency, hands/faces, text artifacts. Choose on accepted-images-per-
dollar, not headline price, because the handbook budgets 30-60% rejection on
early runs.

Prompt construction, straight from the handbook: style lock verbatim, shot size,
subject with kit specifics, location, action, lighting, atmosphere, a
period-accuracy note, "no text, no watermark", plus the negative list. Store
prompt and seed per scene so one failed scene can be regenerated alone.

Consistency: one style lock per video, a fixed seed family, and reference images
for recurring characters and locations (image-to-image from the accepted
reference, never from another generated output).

Hard bans, enforced at the review gate: a synthetic face on a named historical
figure presented as real, invented artifacts, modern kit, and baked-in text.
`youtube.contains_synthetic_media` must be `true` whenever a generated still
ships.

Cost `ESTIMATE`: 30 scenes × 30% generated × 2× rejection = ~18 generations.
At $0.03-0.10 per image that is $0.5-2 per video; local generation is GPU minutes.

## 6. Motion: cheapest perceived-quality jump

The manifest already carries `camera`, and assemble already does zoompan — it
simply is not reading the field. Wiring those together is the highest
quality-per-line change in this plan.

- `slow_push` 1.00→1.08 over the scene, `pan_left`/`pan_right` ±4% horizontal,
  `tilt_up` ±3% vertical, `static` for typographic beats.
- Parallax next: split accepted stills into foreground and background, move them
  at different speeds. This is the trick that separates "documentary" from
  "slideshow" in the rubric.
- Crossfade 200ms between sections, hard cuts inside a section.
- Image-to-video only for a single hero beat per video, ≤4s, generated from a
  locked still. Never for maps, never for crowds — the handbook's failure table
  is explicit about face and weapon drift.

## 7. Tier C: maps

Maps are the strongest retention-per-effort item for military and geography
topics, and they are fully self-made, so no rights questions.

- Phase 1: static map still — public-domain base map, vector route and arrow
  overlays rendered locally (Pillow or cairo), labels via `drawtext`.
- Phase 2: animated routes by compositing the overlay frame-by-frame with
  ffmpeg; phase 3 Manim if it is installed.
- One map per video minimum. The Teutoburg video wants the forest road net and
  one arrow converging on Kalkriese.

## 8. QA integration

Automatic, in `qa.py`:

- every scene has an asset and `asset_status == selected`
- resolution ≥1600×900 and the crop does not cut the subject
- license allowed, plus non-empty credit for every non-generated asset
- phash duplicates across scenes fail
- a shipped card fails a release build unless explicitly allowed
- text-in-image detection if tesseract is present, `not_available` otherwise —
  never a silent pass

Human gate: a new `approve-visuals` command prints a contact sheet with each
scene's asset, credit and prompt, then pins the asset hashes. `approve-render`
refuses a release build without it, the same shape as the script gate.

Description: a "Visual credits" block generated from `assets.json`.

## 9. Slices, in order

Each slice lands with tests and leaves the pipeline runnable.

1. Manifest visual spec plus query builder, no network, unit-tested.
2. Per-scene Commons fetch: three queries, filters, deterministic pick, mapping
   to `scene_id`, `visuals-fetch --all`; tests run against recorded fixtures.
3. Second archival provider behind an `ArchiveProvider` interface (Met or AIC),
   with license and credit tests.
4. Camera field → zoompan parameters, plus motion tests.
5. `ai_image` provider interface, first route, prompt builder, disclosure wiring.
6. Map still renderer and one route CLI, tested on a fixture overlay.
7. QA additions, `approve-visuals` gate, credits block.
8. Bake-off script and a per-video `assets_report.json` (generated, accepted,
   rejected, spend).

Slices 1-4 give a legally clean, period-correct video with motion and no AI
spend. That is the state worth shipping first.

## 10. Acceptance for the next Teutoburg render

- ≥80% of scenes have period-correct non-card assets.
- Zero license violations; every credit present in the description.
- No placeholder card and no wrong-era image, signed off by a human at G2.
- Export unchanged: 30.000 fps, -14 LUFS, peak ≤ -1 dBTP.
- Rubric minimums for the visual categories: visual quality ≥6, authenticity
  ≥6, continuity ≥6, animation ≥6.

## 11. Risks and their guards

| Risk | Guard |
|---|---|
| Commons rights metadata is wrong | record raw license and URL, review flagged assets at G2, no bulk scraping |
| AI invents period kit | handbook accuracy notes per scene, 30-60% rejection budget, human check on hero shots |
| The viral look depends on copyrighted film stills | not used; archival tier only accepts the allowlist |
| Cost creep from regeneration | per-video spend cap in config, phash dedupe, prompt-hash cache |
| Network sources are not reproducible | pin sha256, URL and license in `assets.json`; selections survive reruns |

## 12. What not to do

Do not scrape image search results. Do not use documentary stills, film frames or
news photos as "historical footage". Do not generate a real named person saying
something they did not say — it is an auto-rejection in the repo's own rubric and
a platform-policy violation. Do not ship a generated still without the synthetic
media disclosure.
