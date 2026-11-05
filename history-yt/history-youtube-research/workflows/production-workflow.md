# Production Workflow

End-to-end pipeline for one history video. It uses the existing histyt pipeline where the pieces fit: topic registry, research, script, script approval gate, voice, visuals, assemble, thumbnail, render approval gate, private upload. Manual steps sit at gates, not everywhere.

## Stage 1. Topic discovery

Input: niche deltas, search demand, prior video performance, source availability.
Output: 5 to 10 candidate topics with a one-line question each.
Tools: YouTube search, Google Trends, chapter-title mining from the benchmark channels, comment mining for questions viewers keep asking.
Can automate: title and chapter scraping, demand ranking.
Human review: pick 3 topics you can source. Reject topics where the only sources are other YouTube videos.

## Stage 2. Topic evaluation

Score each candidate on: search demand, visual potential, source depth, your ability to tell it in 8 to 12 minutes, and whether a clip can stand alone as a short.
Output: one selected topic, one backup.
Failure point: topics with drama but no sources. Drop them.

## Stage 3. Historical research

Gather 5 to 10 quality works before writing. Record page-level references. Extract a dated timeline and mark consensus, contested, and unknown links.
Output: `sources.json` plus a claim table.
Failure point: citing a secondary source that is itself wrong. Check the primary source for any number that carries the story.

## Stage 4. Source verification

Tag every factual sentence [A] verified, [B] single source, [C] contested, [U] unknown. Convert [B] or find a second source. Convert [C] to honest on-camera framing.
Output: verified claim table with conflicts listed.
Human review: required. This is the script approval gate in the pipeline.

## Stage 5. Story development

Write the central question in one sentence. Cast the protagonist, the opposing force, and the person who bears the cost. Mark act turns on the timeline.
Output: beat sheet with chapter titles.
Automate: beat timing from word count.
Human review: if the question cannot be written in one sentence, stop.

## Stage 6. Scriptwriting

Write for the ear. No semicolons. Spell out numbers under 100. Mark pauses and emphasis. Keep one idea per sentence.
Output: voiceover script with visual annotations inline.
Word count: about 150 to 160 words per minute for documentary pace.

## Stage 7. Script review

Read the script aloud. Cut 15 to 20%. Verify the hook lands inside 30 seconds. Verify the ending answers the opening question.
Output: locked script.
Gate: script approval. Do not generate visuals against an unlocked script.

## Stage 8. Scene planning

Turn each script paragraph into a scene record: narration, period, location, subjects, environment, composition, camera, lighting, atmosphere, action, style, duration, transition, continuity references. See the AI Visual Production Handbook.
Output: `scene_manifest.json`.
Automate: draft scene records from script paragraphs.
Human review: anachronism check on every scene before generation.

## Stage 9. Character and location sheets

Generate one turnaround sheet per recurring character and 2 to 3 establishing stills per recurring location. Lock the style token set once per video.
Output: reference images in `refs/`.
Automate: sheet generation, seed logging.
Human review: compare every sheet against period references.

## Stage 10. Image generation

Use the prompt template from the handbook. Same stem every time. Only action and camera change.
Output: approved stills.
Budget: expect 30 to 60% rejection on early runs. Regenerate only failed scenes.
Failure points: extra fingers, wrong crest direction, anachronistic architecture, face drift.

## Stage 11. Video generation

Order of preference: still plus parallax layers, then slow push or pan from an approved still, then subject animation only where the story needs it. Maps are pre-rendered 2D animation, not image-to-video.
Output: short clips, 4 to 6 seconds each.
Budget: a Kling v3 clip at $0.14 per second costs $0.56 to $0.84 for 4 to 6 seconds before regeneration. A full video of 60 clips is not viable on an API budget. Use stills and parallax for most scenes.

## Stage 12. Narration

Generate voice from the locked script. Check every non-English name against the pronunciation sheet. Regenerate only the wrong lines.
Output: `audio/chunk_NNN.wav` plus `durations.json`.
Gate: listen to the full track before editing.

## Stage 13. Music and sound effects

Select music with a recorded license. Record the license per track. Add room tone, crowd noise, and battle sounds only where they support a scene.
Output: music and SFX stems with license notes.
Automate: ducking music under voice, silence detection.
Human review: check that no unlicensed track slipped in.

## Stage 14. Editing and assembly

Assemble with the existing pipeline: segments, video, final. Use FFmpeg for loudness at -14 LUFS integrated and true peak at or below -1 dBTP. Correct auto-captions, especially names. Add chapters from the beat sheet.
Output: `final.mp4`, `captions.srt`.
Automate: export, loudness check, thumbnail rendering.

## Stage 15. Quality control

Score the video against the QA checklist. Two reviewers score accuracy, hook, and cohesion independently. Any auto-rejection item stops release.
Output: scored checklist and `qa_scores.jsonl` entry.
Gate: render approval.

## Stage 16. Thumbnail and title

Generate 3 thumbnail candidates from the locked style. Test the squint test and the truth test. Score 5 title options against the formulas in the master report.
Output: `thumbnail.png` and `thumbnail_meta.json`.
Human review: title must match the video. No claim the video does not support.

## Stage 17. Publishing

Upload as private. Set the AI disclosure correctly for realistic generated scenes. Add chapters, sources, and the correction invite in the description. Publish after the render gate.
Output: `upload.json`.

## Stage 18. Performance analysis

After 48 hours and 14 days, record views, average watch duration, and traffic sources. Compare against the channel median, not the top video. Log what the hook and title did versus the plan.
Output: performance log appended to the project record.
Use: feed the topic evaluation stage for the next video.

## Agent notes

The pipeline already has approval gates in the histyt CLI. Keep them. Automation belongs in extraction, assembly, loudness, captions, and exports. Editorial judgment stays human: topic choice, claim verification, hook strength, and final release.
