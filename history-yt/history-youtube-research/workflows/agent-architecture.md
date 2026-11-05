# Agent Architecture

Small set of specialist agents. Each agent has one job, a defined input, a defined output, and a quality gate. Consolidate where a simpler system holds up. Do not add agents for their own sake.

## Research Agent

- Task: gather sources for a topic.
- Input: topic and central question.
- Output: source list with page-level references, a dated timeline, and a conflict list.
- Success criteria: 5 to 10 quality sources, every contested claim flagged, nothing sourced only from YouTube or Wikipedia.
- Tools: web search, browser extraction, document summarizers.
- Gate: human checks the source list before the Fact-Checking Agent runs.

## Historical Fact-Checking Agent

- Task: verify claims in a draft script.
- Input: draft script plus the claim table from Research.
- Output: tagged claims [A], [B], [C], [U], with corrections and on-camera handling notes.
- Success criteria: every quantitative claim has one or two sources as required, no invented quotes, no unsourced numbers.
- Tools: source search, citation matching.
- Gate: human approves. This agent does not clear a script on its own.

## Story Development Agent

- Task: turn verified research into a beat sheet.
- Input: claim table and timeline.
- Output: central question, cast, act turns, chapter titles.
- Success criteria: the question fits in one sentence, each chapter changes the stake, the ending answers the opening question.
- Gate: human approves the beat sheet.

## Scriptwriter Agent

- Task: write the voiceover from the beat sheet.
- Input: beat sheet, template length, word count target.
- Output: script with inline visual annotations and pronunciation notes.
- Success criteria: reads aloud cleanly, 150 to 160 words per minute, hook inside 30 seconds, no semicolons, numbers under 100 spelled out.
- Gate: human reads aloud.

## Script Editor Agent

- Task: tighten a script and check it against the QA rubric.
- Input: draft script.
- Output: revised script with a change log.
- Success criteria: 15 to 20% shorter if possible, every fact still tagged, no hook over a reveal.
- Gate: human locks the script.

## Visual Director Agent

- Task: design the look of the video.
- Input: locked script, period list, character list.
- Output: style lock, character sheets, location sheets, scene records.
- Success criteria: one style set per video, period-plausible costume and architecture, continuity references for every recurring character and place.
- Gate: human checks sheets against period references.

## Scene Prompt Generator

- Task: turn scene records into generation prompts.
- Input: one scene record.
- Output: positive prompt, negative prompt, model settings, reference image list, seed.
- Success criteria: same prompt stem across scenes, only action and camera change, anachronism terms in the negative prompt.
- Gate: human reviews the first batch.

## Image and Video Generation Agent

- Task: run generation and track results.
- Input: prompt package.
- Output: stills and clips logged in `scene_manifest.json` with model, prompt, seed, and approval status.
- Success criteria: approval rate tracked, failed scenes regenerated only, no unlogged output used in the edit.
- Budget rule: API spend is capped per scene. Local generation is used for drafts.

## Narration Agent

- Task: produce the voice track.
- Input: locked script and pronunciation sheet.
- Output: audio chunks, durations, chapter timings.
- Success criteria: every name pronounced correctly, no clipped lines, total duration matches the script.
- Gate: human listens to the full track.

## Editing Assistant

- Task: assemble the video.
- Input: approved stills, clips, narration, music, SFX.
- Output: `final.mp4`, `captions.srt`, `thumbnail.png`.
- Success criteria: loudness -14 LUFS, true peak at or below -1 dBTP, captions match narration, no black frames, no placeholder text.
- Gate: export runs automatically, release does not.

## Quality Assurance Agent

- Task: score the finished video.
- Input: release candidate video, script, sources, licenses.
- Output: scored checklist, pass or reject verdict.
- Success criteria: every auto-rejection item checked, scores logged to `data/qa_scores.jsonl`, disagreements flagged.
- Gate: two humans sign off.

## Thumbnail and Title Agent

- Task: produce packaging options.
- Input: video summary, hook, style lock.
- Output: 3 thumbnail candidates, 5 title options with the formula used.
- Success criteria: passes the squint test and the truth test, no claim the video does not support.
- Gate: human picks.

## Consolidation notes

The Fact-Checking and Script Editor agents can merge into one pass once the claim tagging is stable. The Visual Director and Scene Prompt Generator can merge once style locks are templated. Keep Research, Fact-Checking, Scriptwriter, Generation, Narration, Editing, QA, and Packaging separate. Each one fails in a different way, and a merged agent hides the failure.

## Handoffs

research → fact-check → story → scriptwriter → editor → visual director → prompt generator → generation → narration → editing → QA → packaging → publish → performance log. Every handoff is a file, not a conversation. If a file is missing, the stage did not happen.
