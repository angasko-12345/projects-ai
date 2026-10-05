# TikTok Slop Factory

Tiny MVP that generates vertical TikTok-style videos from short fictional
story scripts. One command in, playable MP4s out.

Everything runs on free tiers, and the only external dependency is **one text
backend** — pick one:

| Backend | Ideas / script / caption | Narration voice | Needs a key |
|---|---|---|---|
| `gemini` (default) | Gemini API | Gemini TTS | `GEMINI_API_KEY` |
| `local` | `local_text.json` on disk | Edge TTS (free) | no |

Visuals are generated locally with FFmpeg, so no stock-media API is required.
There is no database, no dashboard, no Docker, and no browser automation.

All content is explicitly fictional. Nothing here is presented as real news.

## What it does

1. Asks the chosen backend for a batch of story ideas.
2. Turns each idea into a short script (hook, story, twist, ending, CTA).
3. Speaks the script with the backend's TTS.
4. Generates animated visuals from the script with FFmpeg.
5. Burns readable subtitles over the visuals and muxes the narration.

Output is 1080x1920 (9:16), H.264/AAC, yuv420p, faststart, with burned-in
subtitles and a real audio track.

Both backends go through the identical pipeline — ideas -> script -> TTS ->
duration/coherence gate -> visuals -> captions -> MP4 -> metadata. Only the
text and voice providers differ.

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.10+ | 3.11/3.12/3.13/3.14 all work |
| FFmpeg **and** ffprobe | Both ship together; both must be on `PATH` |
| A Gemini API key | Only for `--backend gemini`. Free tier: <https://aistudio.google.com/apikey> |

With `--backend local` there is no key of any kind; `edge-tts` and a text file
are the whole dependency list. No stock-media or image API either way.

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

### 4. Configure your key (Gemini backend only)

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in:

```
GEMINI_API_KEY=your_key_here
```

That is the only key the Gemini backend needs. `.env` is git-ignored. Never
commit it. To use `--backend local` instead, skip this step entirely — no
`.env` and no key are required.

## Usage

```powershell
# Gemini backend (needs GEMINI_API_KEY)
python make_videos.py --dry-run                # check config and FFmpeg
python make_videos.py --count 1
python make_videos.py --count 3

# Local backend (no key at all)
python make_videos.py --backend local --dry-run
python make_videos.py --backend local --count 1
```

`--backend` accepts `gemini` or `local`; anything else is rejected with the
valid choices. Omit it and the `TEXT_BACKEND` environment variable decides,
falling back to `gemini`. So `set TEXT_BACKEND=local` makes the flag
unnecessary, but the flag wins when both are present.

Exit codes: `0` all good, `1` setup failure or nothing produced, `2` partial
success (some videos failed). A single bad video never aborts the batch.

## The local backend

`--backend local` replaces the Gemini API with two local substitutes:

* **Text** comes from `local_text.json` (override with `TEXT_PROVIDER_JSON`).
  It holds one `scripts` array of complete scripts:

  ```json
  {
    "scripts": [
      {
        "idea": "one line, 6-12 words, clearly fictional",
        "hook": "lands in the first 10-15 words",
        "story": "110-150 words across story + twist + ending",
        "twist": "the turn",
        "ending": "the close",
        "cta": "a question for the viewer",
        "title": "shown in metadata",
        "hashtags": ["three", "to", "five"]
      }
    ]
  }
  ```

  Missing file, malformed JSON, a missing/empty `scripts` array, an entry that
  is not an object, or a script with no narration text each produce an error
  naming the file, the problem, and what to do about it — not a traceback.

* **Narration** comes from Microsoft Edge TTS (`edge-tts`), which is free and
  needs no account. Voice and rate are `EDGE_TTS_VOICE` and `EDGE_TTS_RATE`;
  they are separate from `TTS_VOICE` because that one names a Gemini voice.

One `--count N` request draws from the first N scripts, so each request is one
video. Beyond the scripts in the file, ideas repeat — the backend does not
invent stories. Adding more scripts is a file edit, not an API call.

## Output layout

```
output/
  videos/     <NN>-<slug>-<hash>.mp4    <- the deliverable
  audio/      narration
  captions/   burned-in subtitle source (.srt)
  metadata/   title, script, caption, hashtags, scene recipes, duration
  visuals/    generated silent visual track (safe to delete; re-rendered)
```

Filenames use a stable SHA-256 digest, so the same idea always produces the
same filename. Re-running is idempotent instead of creating duplicates.

## Configuration

Everything is an environment variable. Only the Gemini key is required, and
only by the Gemini backend.

| Variable | Default | Purpose |
|---|---|---|
| `TEXT_BACKEND` | `gemini` | Backend used when `--backend` is absent |
| `GEMINI_API_KEY` | *required for `gemini`* | Gemini API key |
| `GEMINI_TEXT_MODEL` | `gemini-3.8-flash` | Ideas and scripts (gemini) |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-lite-tts` | Narration (gemini) |
| `TTS_VOICE` | `Kore` | Prebuilt Gemini voice name (gemini) |
| `TEXT_PROVIDER_JSON` | `<root>/local_text.json` | Local scripts file (local) |
| `EDGE_TTS_VOICE` | `en-GB-SoniaNeural` | Edge TTS voice (local) |
| `EDGE_TTS_RATE` | `+8%` | Edge TTS speaking rate (local) |
| `FFMPEG_PATH` | `ffmpeg` | FFmpeg executable |
| `FFPROBE_PATH` | `ffprobe` | ffprobe executable |
| `VISUAL_FONT` | auto-detected | Bold TTF used for on-screen scene keywords |

`TEXT_PROVIDER_JSON` resolves relative to the project root, not the working
directory, so the CLI behaves the same from anywhere. `--backend` overrides
`TEXT_BACKEND`; they never conflict silently.

`VISUAL_FONT` is only needed on systems where no common bold font is found;
Windows, most Linux distros, and macOS are detected automatically. If no font
is available the keywords are simply skipped and the rest still renders.

## How backend selection works

`make_videos.py --backend NAME` resolves to one module and hands it to the
pipeline:

| Name | Module | Implements |
|---|---|---|
| `gemini` | `app/gemini.py` | `generate_ideas`, `generate_script`, `script_to_text`, `generate_tts`, `generate_caption` |
| `local` | `app/local_text.py` | the same five functions, plus `check_setup()` |

`app/providers.py` maps the name to the module and imports it lazily, so a
missing optional dependency in one backend cannot break the other. The
pipeline takes the module as an argument; nothing is monkeypatched and the
Gemini path is unchanged by construction.

Each backend also supplies `check_setup()`, so `--dry-run` reports that
backend's own problems — a missing local text file or `edge-tts` for `local`,
a missing `GEMINI_API_KEY` for `gemini` — instead of checking the wrong one.

## How the visuals are generated

The script is split into scenes, and each scene becomes an independent recipe:
its own text, duration, colour palette, visual style, camera motion, and
transition into the next scene.

| Style | What it draws |
|---|---|
| `nebula` | Blurred Mandelbrot fractal screened over an animated gradient |
| `cells` | Cellular automaton, heavily blurred, soft-lit into the gradient |
| `aurora` | Sweeping hue and heavy blur for soft light bands |
| `shards` | Drifting geometric grid |

Camera motion is `push_in`, `pull_out` (both `zoompan`), `pan_left`,
`pan_right`, or `drift` (a sine wander). Scenes are joined with `xfade`
transitions that land on the narration's scene boundaries, so the cuts follow
the voice rather than a fixed interval. Every scene also gets drifting
particles, film grain, a vignette, and its own keywords rendered as on-screen
text.

The whole track is one FFmpeg `filter_complex`, so there are no intermediate
image assets on disk.

Model IDs are configurable because Google retires models on a schedule. If a
call starts failing with `404` or `NOT_FOUND`, update the two model variables
to the current IDs from <https://ai.google.dev/gemini-api/docs/models>.

## How the caption timing works

Captions used to divide the narration duration evenly across the words, which
drifts as soon as the speech is not perfectly uniform. Each word now carries a
weight, and the probed narration duration is shared out in proportion:

* `1.0` for being spoken at all;
* `+0.35` per syllable beyond the first, estimated from vowel groups;
* `+0.50` for a comma, `+0.70` for `;` or `:`, `+0.90` for `.`, `?`, `!`, or an
  ellipsis, credited to the word carrying it.

So "extraordinarily" earns more time than "cat", and a word before a comma
earns more than the same word mid-sentence. Captions never merge across a
sentence boundary, because the pause there is real time.

Two bounds keep the result watchable. A caption shorter than `MIN_CUE_SEC`
(0.40s) is unreadable, so a narration too short for its own word count raises
`CaptionTimingError` instead of emitting a strobe; a caption longer than
`MAX_CUE_SEC` (7.00s) stops being a caption, so a long narration over very few
words is capped and the track ends early rather than stretching.

This is a heuristic for reducing caption drift, **not** speech alignment — no
model, no audio analysis, and no per-language speaking rate is involved, and no
fixed words-per-second figure is assumed. It decides only how the *measured*
narration length is distributed; the probed duration always wins. The model is
deterministic and pure Python, so identical text and duration give identical
timestamps.

## Narration coherence is checked before rendering

Visual rendering is the expensive stage, so the pipeline validates the narration
the moment it has both the script and the TTS audio — before any frames are
drawn. Two failures stop a video that would have been obviously broken:

* **Silence.** A file can have a perfectly valid duration and still contain no
  voice. FFmpeg's `volumedetect` filter measures the mean level, and anything at
  or below **-80 dBFS** is rejected as silent or near-silent. The threshold sits
  in a measured gap: digital silence reads exactly -91 dB on 16-bit PCM, while
  even a heavily attenuated -60 dB tone reads about -78 dB and ordinary speech
  peaks near -18 dB. Quiet narration is therefore never rejected for being
  quiet; only genuinely empty audio is.
* **Duration mismatch.** 200 words of script compressed into 3 seconds is not
  narration, and a 90-second padded track for a short script is not either. The
  script implies a range, and the probed duration has to fall inside it.

The expected range comes from the same caption weights described above, so
there is one speech model rather than two, and it is a **heuristic band, not
speech alignment**: `0.40s` per weight unit, scaled by a wide `0.6x`–`2.0x`
tolerance. No language, accent, or delivery speed is modelled, and no
words-per-second constant is claimed to be exact. The tolerance is deliberately
loose — this catches impossible pairings, not slow voices.

Neither failure ever modifies the audio: nothing is stretched, truncated, or
re-timed to make the numbers agree. The narration is rejected and regenerated
instead, and the error names the measured duration, the expected range, and what
to do about it. If a narration passes this gate but the caption bounds still
cannot represent its full length, that is a separate caption-policy question —
this gate does not paper over it by touching the audio.

## Tests

```powershell
python -m pytest -q
```

The suite covers parsing, retries, filename determinism, caption timing, and
backend selection (local, Gemini, invalid names, and local-data errors)
without network access. Renderer tests shell out to real FFmpeg and are
skipped automatically when FFmpeg is not on `PATH`.

## Troubleshooting

**`Unknown backend 'x'`** — `--backend` takes `gemini` or `local`. The same
check applies to `TEXT_BACKEND`.

**`Local text file not found`** — `TEXT_PROVIDER_JSON` points at a file that
is not there. Copy `local_text.json` or set the variable to your own. The
error prints the full path it tried.

**`Local text file is unusable`** — the file exists but is not the shape the
backend needs. The message names the exact problem (bad JSON, no `scripts`
array, empty array, an entry that is not an object, a script with no
narration). Fix the JSON rather than deleting the file.

**`edge-tts is not installed`** — `pip install -r requirements.txt`. Only
`--backend local` needs it.

**`Edge TTS failed for voice ...`** — either no network access or a voice name
Edge does not offer. List them with
`edge-tts --list-voices`; set `EDGE_TTS_VOICE` to one of them.

**`FFmpeg not found`** — reopen PowerShell after installing, or set
`FFMPEG_PATH` and `FFPROBE_PATH` in `.env` to absolute paths.

**`Gemini HTTP 404`** — the model ID was retired. Update `GEMINI_TEXT_MODEL`
and/or `GEMINI_TTS_MODEL`.

**No on-screen keywords** — no usable bold font was found. Set `VISUAL_FONT`
in `.env` to the full path of a bold `.ttf`.

**Scenes render but look static** — check that FFmpeg was built with the
`gradients`, `zoompan`, and `xfade` filters (`ffmpeg -filters`). A minimal
build without them will fail loudly rather than render black.

## Scope

Deliberately not included, and not planned: database, dashboard, browser
automation, TikTok uploader, Docker, agent orchestration. Keep it small.
