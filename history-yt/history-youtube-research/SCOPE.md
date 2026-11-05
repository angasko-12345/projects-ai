# Research Scope — History YouTube Production System

**Date established:** 2026-11-05
**Task:** Deep research on successful History YouTube channels → actionable, AI-assisted production system.
**Output root:** `history-youtube-research/` (this directory)

## Tooling note (transparency)

The task requested "playwright mcp". No Playwright MCP server is configured in this
environment (verified: `opencode.list_mcp_resources` returns no playwright server; no
playwright entry in Code Mode catalog; `.playwright-mcp/` folder in workspace root only
holds old logs). Substitute used: **agent-browser 0.38.2** (CDP-based Chrome automation
CLI, installed on this machine) + `webfetch`/`websearch` + `context7` for library docs.
YouTube pages are client-side rendered, so data was extracted from the
`ytInitialData` / `ytInitialPlayerResponse` objects embedded in channel/video pages via
browser `eval`, plus `agent-browser read` for prose pages. All statistics were captured
on **2026-11-05** and are labeled verified (browser-extracted), estimate, or unknown.

## Research questions

1. Which History channels (established + fast-growing, ≥20) are winning, and why? (Phase 1)
2. What do ≥60 high-performing videos from those channels do in hooks, structure,
   visuals, pacing, editing, engagement? (Phase 2)
3. Which patterns recur, and which are confounded? (Phase 3)
4. How should scripts be written and verified for accuracy? (Phase 4)
5. Which AI image/video/voice/audio tools (as of 2026-11-05) actually work, what do
   they cost, what are rights/limits? (Phase 5–6)
6. What title/thumbnail systems are demonstrated in the data? (Phase 7)
7. How to automate discover → publish with human gates? (Phase 8)
8. How to score/reject videos before release? (Phase 9)
9. What is the recommended blueprint? (Phase 10)

## Constraints honored

- No existing project code/config modified. Research/documentation only.
- No invented metrics. Unverifiable = "unknown" or "estimate".
- Retention-related claims labeled as hypotheses (public data ≠ retention).
- Transferable principles extracted; no copying of any channel's scripts, branding, or identity.
- Historical accuracy treated as a first-class quality gate.

## Deliverable index (Phase 10)

See `README.md`. Status tracked in each file; a phase is not "complete" until its
deliverable exists and was re-read/checked.
