# Phase 9: Quality Evaluation Framework

Scoring a finished video before release. Every category is scored 1-10 by a human
reviewer unless marked (auto). Historical accuracy and narrative quality are
weighted as heavily as visuals. A video that looks great and tells it wrong does
not pass.

## 1. Score rubric (1-10 per category)

| Category | 1-3 (unacceptable) | 4-6 (mediocre) | 7-8 (good) | 9-10 (excellent) |
|---|---|---|---|---|
| Historical accuracy | Material errors: wrong dates, rulers, battles, invented quotes presented as real | Minor errors or unsourced central claims | Claims sourced; conflicts handled honestly; uncertainty flagged in narration or description | Multi-source verified; primary sources cited where available; interpretations labeled as such |
| Script quality | No clear arc; facts listed without causality | Serviceable but flat; stakes unclear | Clear conflict, rising stakes, satisfying resolution | Crafted scenes, strong causality, emotional throughline, quotable clarity |
| Opening hook (first 30s) | Slow preamble; viewer given no reason to stay | Context dump; interesting question only after 60s+ | Question/event/stakes in first 15s; context by 30s | Cold open lands in 5s; question reframed later; new loop opened before each close |
| Visual quality | Incoherent style; artifacts; wrong scale | Recognizable but generic AI slop | Consistent style; plausible detail; deliberate color and composition | Distinctive, cohesive, deliberate cinematography; reads as a documentary, not a slideshow |
| Historical authenticity | Anachronisms (zipper on Roman toga) | Mostly plausible; a few tells | Accurate costume/architecture/weaponry for depicted scene | Period detail verified against references; sources for reconstruction noted in description |
| Scene continuity | Character/location changes between scenes | Same scene, different face; unnoticeable at small size | Consistent characters and environments across cuts | Reference-sheet consistency; environments revisited match |
| Animation | Slides with drifting blur | Static pans only | Purposeful motion (parallax, map drives, camera moves) | Bespoke motion design supporting story beats |
| Narration | Wrong pronunciations; flat; too fast/slow | Understandable; generic TTS cadence | Clear, paced, confident; names handled correctly | Acted delivery; controlled pauses; emphasis matched to meaning |
| Audio | Clipping; music masks speech | Balanced but anonymous mix | Music sits under voice; SFX support scenes | Intentional sound design; dynamics mirror the story |
| Editing | Random cuts; no rhythm | Functional but monotone pacing | Varied shot length; purposeful transitions; audio sync | Rhythm serves emotion; silence used deliberately; no dead air |
| Overall cohesion | Parts work against each other | Parts coexist | Unified piece | Memorable, rewatchable, trustworthy |

## 2. Minimum acceptable scores

- No category below **5**.
- Historical accuracy, script quality, hook, and narration: minimum **7**.
- Visual quality, authenticity, continuity, animation: minimum **6**.
- Overall cohesion: minimum **7**.
- Average across all 11 categories: minimum **7.0**.

## 3. Automatic rejection criteria (any one = reject, reshoot/fix)

1. A presented fact is false and not flagged as disputed or unknown.
2. A fabricated quotation attributed to a real, named person.
3. Anachronism in a hero shot (costume, weapon, architecture, object).
4. Synthetic face inserted onto a real historical figure to make them say something they did not say (also a YouTube disclosure and trust violation).
5. Voice clone of a real, identifiable person without consent.
6. Music or SFX without a license permitting use.
7. Historical location depicted with a landmark that did not exist at the depicted time, presented as real (e.g., Colosseum before 80 AD, Great Pyramid with smooth casing stones intact and golden tip claimed as certain).
8. Numbers without any source (invented casualty figures, invented population counts presented as fact).

## 4. Human review checkpoints

| Gate | When | Reviewer checks |
|---|---|---|
| G1 Script lock | before any generation | accuracy pass, hook strength, length, reading time |
| G2 Still/asset lock | after ~20% of images | style coherence, anachronisms, character consistency |
| G3 Rough cut | before final polish | pacing, dead air, narration sync, transitions |
| G4 Release review | final file | full checklist below; two people sign off |

Automated checks (non-negotiable, machine-run): loudness (-14 LUFS integrated, true peak ≤ -1 dBTP), black frames, silence gaps > 2s outside intentional pauses, caption/subtitle sync, export specs (1080p/4K, correct fps, H.264/HEVC), no placeholder text baked into frames ("lorem", "sample").

## 5. Release checklist

- [ ] Every factual claim in script either sourced (bibliography in description) or labeled interpretation/unknown
- [ ] At least 2 independent sources for any quantitative claim (casualties, dates, populations)
- [ ] Conflicting accounts acknowledged on-screen or in narration
- [ ] Hook delivers curiosity + stakes in first 30 seconds (timestamped check)
- [ ] Every generated face/landmark plausible for the period (still check at full screen)
- [ ] Character/location continuity across all shots
- [ ] Narration pronunciation pass on all names (script annotated with IPA for risky names)
- [ ] Loudness -14 LUFS; no clipping; speech intelligible under music at loudest mix point
- [ ] Captions accurate (auto-captions corrected, especially names)
- [ ] Thumbnail passes squint test and truth test (no misleading imagery)
- [ ] Title accurate; no claim the video does not support
- [ ] AI disclosure set correctly in YouTube Studio (realistic GenAI scenes = Yes)
- [ ] Chapters present and accurate; description includes sources and disclaimers
- [ ] Pinned comment or description line invites constructive correction (for trust and feedback loop)

## 6. Scoring workflow

1. Reviewer A scores all 11 categories independently.
2. Reviewer B scores historical accuracy, hook, and cohesion independently.
3. Any category with >2 point disagreement triggers a discussion and a third read.
4. Scores logged to `data/qa_scores.jsonl` (video id, date, per-category scores, verdict, notes).
5. Verdicts: PASS (meets minimums), REVISE (fixable, list issues), REJECT (auto-rejection or structural failure).

## 7. Calibration

Score two published benchmark videos (e.g., one Kings and Generals documentary and one weaker comparable) with this rubric before first use. The rubric is calibrated when both reviewers land within 1 point on 9 of 11 categories. Repeat calibration quarterly and after any major pipeline change.

Note on retention claims: this rubric uses observable proxies (hook design, chapter density, pacing variance), not private retention analytics. Treat rubric scores as quality correlates, not guarantees.
