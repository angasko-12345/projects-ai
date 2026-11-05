# histyt instructions

Product: topic -> research -> script -> approval gate -> voice -> visuals -> assemble -> thumbnail -> variation -> approval gate -> private upload.

## Identity

Deployment name is Crouton Alpha; presentation name defaults to Crouton Alpha until the client changes it. Code and comments in English. Do not print secrets (doctor shows present/absent only).

## CLI

```powershell
python -m histyt doctor
python -m histyt topic-add "The Fall of Rome" --angle rise_fall
python -m histyt research <id> --url https://example.com --title Example
python -m histyt script <id>
python -m histyt approve script <id>          # gate 1 (script hash pinned)
python -m histyt run <id>                     # voice..variation, stops at render gate
python -m histyt approve render <id>          # gate 2 (render_approved)
python -m histyt upload <id>                  # privacyStatus always "private"
```

Exit codes: 0 ok, 1 error, 2 gate. Global flags: `--config PATH`, `--runs DIR`.

## Tests

```powershell
python -m pytest tests -q
```

## Rules

- Fake providers are the default and the only ones tests use (`llm.provider: fake`, `tts.provider: fake`). No real LLM/YouTube calls in tests.
- Gates: any `script.md` change after approval blocks voice+ (hash mismatch). Upload is blocked until `render_approved`.
- Never upload publicly; `privacyStatus` is the literal `"private"`.
- Synthetic/AI disclosure: the `containsSyntheticMedia` field is config-driven only (`youtube.contains_synthetic_media`). If unset, the CLI prints an ACTION REQUIRED reminder to set the toggle manually in YouTube Studio.
- No silent fallbacks: missing sources, bad licenses, unknown providers raise `PipelineError` with a clear message.
- Caches live on D:: `PIP_CACHE_DIR=D:\tmp\pip-cache`, `TEMP/TMP=D:\tmp`. Do not push to git; do not change git config.
