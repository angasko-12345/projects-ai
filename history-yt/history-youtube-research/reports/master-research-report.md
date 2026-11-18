# Master Research Report

History YouTube production system. Evidence from 21 channels and 60 videos, captured 2026-11-05.

## Evidence labels

VERIFIED = extracted or read from a primary source on 2026-11-05. ESTIMATE = derived, method stated. UNKNOWN = not verifiable with the tools used. HYPOTHESIS = a reading of public data, not private analytics.

## Phase 1. Channel benchmark

### Method

Channel pages were loaded in a browser (agent-browser 0.38.2). Subscriber counts, video counts, joined dates, total views, recent uploads, and popular uploads were read from the page data and from the About tab. Subscriber and view numbers are VERIFIED as shown on 2026-11-05.

### Results

| Channel | Subs | Joined | Total views | Videos | Recent median views | Typical length |
|---|---|---|---|---|---|---|
| The Infographics Show | 15.5M | Feb 27, 2011 | 7.02B | 6,373 | 473,500 | 949s |
| OverSimplified | 9.68M | Oct 28, 2006 | 1.44B | 33 | 39.5M | 1,192s |
| Timeline | 5.91M | Jan 17, 2017 | 1.09B | 1,504 | 132,000 | 2,855s |
| Simple History | 5.12M | Oct 2, 2014 | 1.71B | 1,419 | 278,500 | 581s |
| Extra History | 4.63M | Aug 6, 2011 | 1.78B | 1,354 | 260,500 | 1,086s |
| Kings and Generals | 4.22M | May 8, 2016 | 1.10B | 2,285 | 393,000 | 1,198s |
| Epic History TV | 3.2M | Mar 10, 2015 | 450M | 387 | 699,000 | 1,718s |
| AlternateHistoryHub | 2.49M | Dec 26, 2012 | 445M | 287 | 1.1M | 1,431s |
| Alt Shift X | 2.04M | May 8, 2012 | 471M | 221 | 1.05M | 1,903s |
| History Hit | 2.02M | Jul 4, 2008 | 385M | 851 | 199,500 | 2,310s |
| MagnatesMedia | 1.86M | Dec 18, 2018 | 195M | 255 | 820,000 | 1,914s |
| The History Guy | 1.65M | Jan 30, 2017 | 438M | 2,257 | 49,000 | 1,116s |
| Fall of Civilizations | 1.52M | Jan 20, 2019 | 226M | 47 | 4.15M | UNKNOWN |
| Historia Civilis | 1.09M | Aug 11, 2014 | 162M | 89 | 1.9M | 1,554s |
| History Dose | 860K | Jun 22, 2018 | 76M | 130 | 352,000 | 924s |
| Military History Visualized | 787K | Jan 5, 2016 | 143M | 477 | 34,000 | 840s |
| UnchartedX | 758K | Jul 4, 2018 | 72M | 178 | 443,500 | 1,719s |
| BazBattles | 725K | May 10, 2016 | 98M | 90 | 303,000 | 666s |
| Stefan Milo | 655K | Feb 20, 2016 | 98M | 208 | 353,000 | 1,283s |
| Captivating History | 506K | Sep 7, 2017 | 74M | 742 | 2,700 | 840s |
| TimeGhost History | 337K | May 7, 2017 | 48M | 345 | 27,500 | 1,485s |

Source: data/channel_benchmark.csv (21 rows, VERIFIED as shown on 2026-11-05).

### What the numbers show

- Views per subscriber ranges from 89 (History Dose) to 453 (The Infographics Show). Fall of Civilizations sits at 149 despite the highest recent median views. That gap means recent audience trust matters more than the lifetime ratio.
- Average views per video is highest at Fall of Civilizations (4.8M) and OverSimplified (43.5M). OverSimplified's number reflects a catalog of 33 videos. It is not a current output rate.
- Recent median views separate channels by two orders of magnitude. Fall of Civilizations posts 4.15M. Captivating History posts 2,700. The difference is topic selection and series trust, not editing speed.
- Typical lengths cluster between 581 seconds (Simple History) and 2,855 seconds (Timeline). The middle of the field sits at 1,000 to 1,700 seconds.

### Subgenre coverage

The sample covers animated documentary (Kings and Generals, Epic History TV, Extra History), world history (Timeline, Fall of Civilizations, Historia Civilis), military history and battles (Military History Visualized, BazBattles, History Dose), medieval and everyday history (Captivating History, History Hit), environmental and deep history (Stefan Milo), business and biography (MagnatesMedia), world history explainers (Simple History, OverSimplified), alternate history (AlternateHistoryHub), map and territory explainers (Alt Shift X), artifacts and objects (The History Guy), twentieth-century documentary (TimeGhost), and ancient technology claims (UnchartedX).

Full channel detail: data/channel_benchmark.csv.

## Phase 2. Video analysis

### Selection

63 candidate rows were reconciled into 60 unique videos: 59 unique candidate IDs plus one extra verified capture (MvwAWgiERBM, Alt Shift X, The Future of Dunk and Egg). Selection rule: per channel, top popular video, one mid-ranked popular video, and best recent video by views. Files: data/video_analysis.csv and video_analysis.json (60 unique IDs, 21 channels).

### Metadata findings

- 51 of 60 videos have chapter markers. Median chapter count: 8. Median chapter density: 0.32 per minute. Median duration: 1,272 seconds.
- 12 of 60 titles end with a question mark. Question titles are common. They are not the only working pattern.
- Title `How`, `Why`, and `What` openings recur across channels. The Infographics Show and History Dose use them most.
- Description length varies from a few hundred characters to several thousand. Channels that list sources and series links in the description are the ones with the longest descriptions.

### What metadata cannot tell us

Metadata does not show the script, the visuals, the editing, or retention. Hook analysis in data/video_analysis.csv is a proxy from title pattern, first chapter, and description opening. Visual production, AI use, scene pacing, and audience retention are UNKNOWN from metadata. Public view counts do not reveal retention. Treat every retention claim in this report as a HYPOTHESIS.

## Phase 3. Patterns

### Strongly supported (seen in the data)

1. Chapter density between 0.2 and 0.5 per minute is the norm for documentaries longer than 15 minutes.
2. Question-led titles appear in 20% of the sample and correlate with explainer channels.
3. Series numbering in titles (Part 1, Episode 8, #17) is standard in the top 10 channels.
4. Duration clusters around 15 to 25 minutes for established documentary channels.
5. Description boxes carry sources, series links, and correction invites in the strongest channels.

### Promising but less established

1. Map-first openings. Kings and Generals and Military History Visualized use maps to carry geography. The metadata supports that these channels use maps. It does not prove the map caused the views.
2. Object-led titles (screwdriver, dummy heads, Panzerfaust). These perform well in the sample but in narrow channels.
3. "Really looked like" phrasing. History Dose uses it repeatedly. Trust signal, not a retention guarantee.

### Subjective creative recommendations

1. Cinematic feel comes from consistent color, one style lock, and deliberate pacing, not from more effects.
2. Historical figures feel human through constraints (what they could not know, what it cost them), not through invented dialogue.
3. Scale, geography, and time read best through maps first, then people, then detail.

### Causation warning

Views correlate with subscriber base, upload age, series trust, and topic. Do not treat a title pattern as the cause of a view count.

## Phase 4. Scriptwriting system

See handbooks/history-scriptwriting-handbook.md. Core rules: never fabricate, no invented dialogue, numbers need sources, suspense from structure, honest hedging reads as confidence.

## Phase 5. AI visual production

See handbooks/ai-visual-production-handbook.md and reports/tool-directory.md.

Decisions:

- Local first with ComfyUI and open weights for drafts and stills.
- fal FLUX for consistent hero stills. FLUX.1 schnell $0.003 per megapixel, FLUX 2 $0.012, FLUX.1 dev $0.025 (VERIFIED, https://fal.ai/pricing).
- Kling v3 at $0.14 per second and FLUX 3 Max at $0.085 per second for 4 to 6 second clips only. A full video cannot be generated clip-by-clip on an API budget.
- Maps pre-rendered in 2D. More reliable than image-to-video for borders and dates.
- One style lock per video. Character sheets before any scene generation.

## Phase 6. Audio

- ElevenLabs Starter at $6 per month for 30,000 credits. Text to speech 1 credit per character. Commercial license included on the Starter tier (VERIFIED, https://elevenlabs.io/pricing).
- Runway Standard at $12 per month billed annually for 625 credits. That equals about 52 seconds of Gen-4.5 or 78 images (VERIFIED, https://runway.com/pricing).
- Piper and Kokoro for $0 marginal cost. Test pronunciation before committing.
- Loudness: -14 LUFS integrated, true peak at or below -1 dBTP.

## Phase 7. Packaging

Ten title formulas and ten thumbnail concepts are in reports/recommended-production-blueprint.md, with five worked pairings. The rules that matter: subject first, turn second, image must pass the truth test, 2 to 4 words on the thumbnail, 60 to 70% of the frame used.

## Phase 8. Workflow and agents

See workflows/production-workflow.md and workflows/agent-architecture.md. Eighteen stages, human gates at script lock, asset lock, rough cut, and release. Automation belongs in extraction, assembly, loudness, captions, and exports.

## Phase 9. Quality

See workflows/quality-assurance-checklist.md. Eleven categories scored 1 to 10. Minimums: no category below 5, accuracy, script, hook, and narration at 7 or better, average 7.0 or better. Eight auto-rejection items, including fabricated quotes, invented numbers, anachronisms in hero shots, and unlicensed music.

## Phase 10. Blueprint

See reports/recommended-production-blueprint.md. Lead with 12 to 20 minute documentaries, one per week, shorts cut from the same footage. Local-first visual stack, ElevenLabs Starter voice, QA gates at four points, performance log at 48 hours and 14 days.

## Gaps and honest limits

- Visual production and AI use per video: UNKNOWN. Metadata does not show it. Frame-level inspection was not completed for all 60 videos.
- Retention: HYPOTHESIS only. Public data does not reveal it.
- Tool pricing: read 2026-11-05. Re-verify before purchase.
- Subscriber and view counts: VERIFIED as shown on 2026-11-05. They change.
- One video in the original candidate list (EMY3CAxTB_c) returned "This video isn't available anymore" on the watch page. It was removed from the sample and the removal is recorded in data/video_selection.md.
