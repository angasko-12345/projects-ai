# TikTok Slop Factory

Tiny MVP that generates vertical TikTok-style videos from short fictional
story scripts. One command in, playable MP4s out.

Everything runs on free tiers: the Gemini API free tier for script and voice,
and the Pexels free API for stock footage. There is no database, no dashboard,
no Docker, and no browser automation.

All content is explicitly fictional. Nothing here is presented as real news.

## What it does

1. Asks Gemini for a batch of story ideas.
2. Turns each idea into a short script (hook, story, twist, ending, CTA).
3. Speaks the script with Gemini TTS.
4. Finds a matching stock clip on Pexels.
5. Burns readable subtitles over the clip and muxes the narration.

Output is 1080x1920 (9:16), H.264/AAC, yuv420p, faststart, with burned-in
subtitles and a real audio track.

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.10+ | 3.11/3.12/3.13/3.14 all work |
| FFmpeg **and** ffprobe | Both ship together; both must be on `PATH` |
| Gemini API key | Free tier: <https://aistudio.google.com/apikey> |
| Pexels API key | Free tier: <https://www.pexels.com/api/> |

## Setup on Windows

Open PowerShell in this folder.

### 1. Create a virtual environment

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, allow local scripts for your user
once:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Prefer not to change the policy? Skip activation and call the interpreter
directly as `.\.venv\Scripts\python.exe` in every command below.

### 2. Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Install FFmpeg

Any of these works. After installing, **close and reopen PowerShell** so the
updated `PATH` takes effect.

```powershell
winget install --id Gyan.FFmpeg --exact
```

Or with Chocolatey:

```powershell
choco install ffmpeg
```

Then confirm it is visible:

```powershell
ffmpeg -version
ffprobe -version
```

If you would rather not touch `PATH`, set the full paths in your `.env`
instead (see `FFMPEG_PATH` / `FFPROBE_PATH` below).

### 4. Configure your keys

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in:

```
GEMINI_API_KEY=your_key_here
PEXELS_API_KEY=your_key_here
```

`.env` is git-ignored. Never commit it.

## Usage

```powershell
python make_videos.py --dry-run     # check config and FFmpeg, make nothing
python make_videos.py --count 1     # one video
python make_videos.py --count 3     # three videos
```

Exit codes: `0` all good, `1` setup failure or nothing produced, `2` partial
success (some videos failed). A single bad video never aborts the batch.

## Output layout

```
output/
  videos/     <NN>-<slug>-<hash>.mp4    <- the deliverable
  audio/      narration
  captions/   burned-in subtitle source (.srt)
  metadata/   title, script, caption, hashtags, footage credits, duration
  footage/    cached Pexels clips (safe to delete; re-downloads on demand)
```

Filenames use a stable SHA-256 digest, so the same idea always produces the
same filename. Re-running is idempotent instead of creating duplicates.

## Configuration

Everything is an environment variable. Only the two API keys are required.

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | *required* | Gemini API key |
| `PEXELS_API_KEY` | *required* | Pexels API key |
| `GEMINI_TEXT_MODEL` | `gemini-3.8-flash` | Ideas and scripts |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-lite-tts` | Narration |
| `TTS_VOICE` | `Kore` | Prebuilt Gemini voice name |
| `FFMPEG_PATH` | `ffmpeg` | FFmpeg executable |
| `FFPROBE_PATH` | `ffprobe` | ffprobe executable |

Model IDs are configurable because Google retires models on a schedule. If a
call starts failing with `404` or `NOT_FOUND`, update the two model variables
to the current IDs from <https://ai.google.dev/gemini-api/docs/models>.

## Tests

```powershell
python -m pytest -q
```

The suite covers parsing, retries, filename determinism, and caption timing
without network access. Renderer tests shell out to real FFmpeg and are
skipped automatically when FFmpeg is not on `PATH`.

## Troubleshooting

**`FFmpeg not found`** — reopen PowerShell after installing, or set
`FFMPEG_PATH` and `FFPROBE_PATH` in `.env` to absolute paths.

**`Gemini HTTP 404`** — the model ID was retired. Update `GEMINI_TEXT_MODEL`
and/or `GEMINI_TTS_MODEL`.

**`Pexels HTTP 401`** — the API key is wrong or expired.

**`Pexels HTTP 429`** — the free tier allows 200 requests/hour. Wait and
retry; the client already backs off automatically.

**`No usable footage found`** — Pexels had nothing for the query. The
pipeline already retries with the raw idea and then a generic fallback; if it
still fails, it is usually a quota or network problem, not a query problem.

## Scope

Deliberately not included, and not planned: database, dashboard, browser
automation, TikTok uploader, Docker, agent orchestration. Keep it small.
