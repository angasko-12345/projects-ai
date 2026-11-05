# histyt instructions

Product: topic -> research -> script -> approval gate -> voice -> scenes -> visuals ->
assemble -> thumbnail -> variation -> QA -> approval gate -> private upload.

## Identity

Deployment name is Crouton Alpha; presentation name defaults to Crouton Alpha until the client changes it. Code and comments in English. Do not print secrets (doctor shows present/absent only).

## CLI

```powershell
python -m histyt doctor
python -m histyt topic-add "The Fall of Rome" --angle rise_fall
python -m histyt research <id> --url https://example.com --title Example --tier secondary
python -m histyt research <id> --fetch-wikipedia "Arminius" --tier secondary
python -m histyt script <id>
python -m histyt approve-script <id>          # gate 1 (script hash pinned, review printed)
python -m histyt run <id>                     # voice..QA, stops at render gate
python -m histyt qa <id>                      # print qa_report.json (exit 2 on reject)
python -m histyt approve-render <id>          # gate 2, refuses on a QA reject
python -m histyt upload <id>                  # privacyStatus always "private"
python -m histyt upload <id> --mock           # no network, no credentials
python -m histyt visuals-fetch <id> --query "Roman legionary relief"
python -m histyt scenes <id>
python -m histyt shorts-plan <id> [--max-clips N]
python -m histyt shorts-extract <id>
```

Exit codes: 0 ok, 2 gate or QA reject, 1 error. Global flags: `--config PATH`, `--runs DIR`.

## Tests

```powershell
python -m pytest tests -q
```

Run ruff with the repo config, since the product's own pyproject does not select rules:

```powershell
python -m ruff check --config ../pyproject.toml histyt tests
```

## Rules

- Fake providers are the default and the only ones tests use (`llm.provider: fake`, `tts.provider: fake`). No real LLM/TTS/YouTube calls in tests.
- `config/real.yaml` is opt-in. It needs `OPENROUTER_API_KEY` and `ELEVENLABS_API_KEY`; never store either in the config or a run file.
- Gates: any `script.md` change after approval blocks voice+ (hash mismatch). Upload is blocked until `render_approved`.
- `approve-render` requires `qa_report.json`. A `reject` verdict blocks it with the failing check ids. Do not bypass the QA gate or fake a report.
- Never upload publicly; `privacyStatus` is the literal `"private"`.
- The script stage owns the length contract: hard error below 60% of the word target. Do not weaken it to make a model output pass.
- Claim grounding is lexical and a heuristic. Keep the labels honest and keep the human script gate in front of it.
- Synthetic/AI disclosure: `containsSyntheticMedia` is config-driven only. If unset, the CLI prints ACTION REQUIRED to set the toggle manually in YouTube Studio.
- No silent fallbacks: missing sources, bad licenses, unknown providers, missing QA reports and unsupported paths raise `PipelineError` or `GateError` with a clear message.
- Caches and models live on D: (`PIP_CACHE_DIR=D:\tmp\pip-cache`, `TEMP/TMP=TMPDIR=D:\tmp`). `config.ensure_d_confined` is the shared policy helper. Do not push to git; do not change git config.
