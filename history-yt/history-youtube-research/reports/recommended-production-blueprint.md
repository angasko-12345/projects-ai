# Recommended Production Blueprint

The production system that best fits a new history channel, based on the 21-channel benchmark and the 60-video sample of 2026-11-05.

## What the data says

- Median video length in the 60-video sample: 21 minutes.
- 51 of 60 videos have chapter markers. Median chapter density is 0.32 per minute.
- 12 of the 60 titles end with a question mark. Question titles are common but not dominant.
- Views per subscriber ranges from 89 to 453 across the 21 channels. The Infographics Show leads at 453. History Dose is lowest at 89.
- Average views per video is highest at Fall of Civilizations (4.8M) and OverSimplified (43.5M). The latter reflects a tiny catalog of 33 videos, not current output.
- Recent median views separate the field sharply. Fall of Civilizations posts 4.15M on recent uploads. Captivating History posts 2,700. Channel trust and topic selection do more work than any editing trick.

Sources: data/channel_benchmark.csv, data/video_analysis.csv and video_analysis.json.

## Channel setup

One presenter voice. One style lock. One hook pattern per series. Documentaries first, shorts cut from the same footage later.

## Format

Lead with 12 to 20 minute documentaries. That matches the median of the sample and leaves room for a map, one turn, and a payoff. Publish one long video per week, then cut 2 to 3 shorts from it. Shorts get the hook and the turn. They do not get the full answer.

## Script

Use the 11-step process in the scriptwriting handbook. One sentence question. Chapter beat sheet before writing. Claim tagging before generation. Reading time matched to shot duration.

## Visual stack

Start local. ComfyUI with FLUX.1 dev or FLUX.2 klein for drafts. Use fal FLUX for consistent hero stills when local quality or speed falls short. Use Kling or FLUX 3 Max for 4 to 6 second motion clips only. Pre-render maps in 2D. That is the whole visual system.

Budget for one video at the API level. At fal prices, 40 images at $0.04 is $1.60. Ten 5-second clips at $0.14 per second is $7.00 before regeneration. A realistic API test budget for one video is $15 to $40. A local workflow costs GPU time and setup, not per-image fees.

## Voice

ElevenLabs Starter at $6 per month for 30,000 credits is the baseline. A 2,500-word script is roughly 15,000 characters, so half the monthly pool. Music and effects draw from the same pool. Piper or Kokoro removes the per-character cost but needs more pronunciation testing.

## Editing

DaVinci Resolve or Kdenlive, FFmpeg for loudness and exports. -14 LUFS. Captions corrected by hand. Chapters from the beat sheet.

## Packaging

Titles: lead with the subject, add the turn. "The Battle That Ended the Empire" beats "History of the Empire." Thumbnails: one focal subject, 2 to 4 words, darkened background, 60 to 70% of the frame used.

Ten title formulas from the sample:

1. "[Event]: The [Consequence] That [Result]" — D-Day 1: The Great Crusade.
2. "How [Subject] [Did the Thing]" — How Rome Conquered Greece.
3. "Why [Subject] Collapsed" — Why the British Empire Fell Apart.
4. "What [Question]" — What Happens When You Die?
5. "[Subject] - Not Quite [Person] Yet" — Catherine the Great: Not Quite Catherine Yet.
6. "The [Superlative] [Noun] in History" — The Biggest Rebellion Against the British Empire in History.
7. "[Person] and the History of the [Object]" — Robertson, Phillips, and the History of the Screwdriver.
8. "[Adjective] [Subject] That Changed [World]" — The Battle of Algiers: France's Victory That Lost the War.
9. "How Did [Subject] Really Look" — What a Samurai vs. Mongol Battle Really Looked Like.
10. "[Number] [Things] That [Proof]" — Is There Scientific Proof of the Bible's Plagues.

Ten thumbnail concepts:

1. Central figure, rim lit, dark background, 3-word text.
2. Map with one glowing route and the title in the corner.
3. Face in profile against a textured parchment field.
4. Two opposing silhouettes with a dividing line.
5. Ruin or battlefield with a single red accent.
6. Object close-up (helmet, coin, ship) with weather behind it.
7. Crowd mass with one face lit.
8. Split frame: before and after the event.
9. Single letter or number with a period backdrop.
10. Poster-style illustration with the series mark small in a corner.

Five title and thumbnail pairings:

1. "How Rome Conquered Greece" + a legionary in lorica segmentata against a dark map. The title promises a mechanism. The image promises a period.
2. "The Biggest Rebellion Against the British Empire in History" + sepoy facing a redcoat officer on a map of India. Specific people, specific place, no generic explosion.
3. "WW2 - OverSimplified (Part 1)" + a duotone battlefield with a simple silhouette. The title does the work. The thumbnail stays readable at small size.
4. "What a Samurai vs. Mongol Battle Really Looked Like" + two warriors in period kit, back to back. The word "Really" is a trust signal. The image must match the kit or it fails the truth test.
5. "The Longest Year in Human History" + a calendar or Julian mechanism detail. A text-led thumbnail can win when the subject is a concept, not a battle.

## QA gates

Script lock, still lock, rough cut, release review. No auto-rejection item ships. Two reviewers on accuracy, hook, and cohesion.

## Publishing

Private upload first. AI disclosure set for realistic generated scenes. Chapters, sources, and the correction invite in the description. Performance log at 48 hours and 14 days.

## First 8 weeks

Week 1: pick the niche slice, record the style lock, test voice and pronunciation.
Week 2: produce video 1 with the full pipeline, accept a slower pace.
Week 3: cut 2 shorts from video 1, publish video 2.
Week 4 to 6: one long video per week, one short per day from delivered footage.
Week 7: review performance logs. Keep the title pattern and hook that worked. Drop the formats that did not.
Week 8: calibrate the QA rubric against two published benchmark videos.

## What not to copy

Do not copy a channel's script, thumbnail style, or series name. Extract the principle (question-led titles, chapter density, map-first geography) and build your own identity.

## Honest limits

View counts do not reveal retention. Chapter density and title patterns are proxies. Tool prices were read on 2026-11-05 and change. Model licenses differ. Re-verify before purchase.
