# Privacy Audit Tool — product instructions

Scope: `small-projects/privacy_audit_tool/` only. The repository-wide contract is
`.agents/AGENTS.md` (read it first); this file adds only what is specific to this
product. There is no product-specific `pyproject` dev tooling, lint, formatter, or
type-check — do not invent one.

## What it is

A read-only, local-first privacy footprint audit for Windows. It scans locally
obtained material — Google Takeout Gmail archives, `.mbox`/`.eml` mail, Chrome,
Edge, Brave, and Firefox history and bookmarks — plus a manually supplied username
list, and writes evidence reports into `output/`. It deletes nothing, changes no
settings, and makes no web requests of its own. User-facing behavior is documented
in the product `README.md`.

## Test command

```powershell
cd small-projects/privacy_audit_tool
python -m unittest discover -s tests
```

- Tests are plain `unittest` (`tests/test_core.py`). Run from the product
  directory so `privacy_audit` imports; `tests/test_run.ps1` is a Windows
  convenience wrapper that sets `PYTHONPATH` and runs the same discovery.
- This product is **not** measured in `.agents/evidence/verification.json`.
  Per the repository rule, no instruction file or report may restate a test
  count for it; run the suite and read its own output.

## Optional dependencies (truthfully)

- Core scanner: Python standard library only. `pyproject.toml` declares no
  runtime dependencies and `requirements.txt` names only the optional extra.
- `maigret` is optional and is used **only** by the explicit
  `--online-usernames` opt-in, which contacts external websites. The test
  suite never needs it: tests stub `run_maigret` instead of importing or
  invoking Maigret. CI deliberately does not install it.

## Privacy and data-safety rules (non-negotiable)

- Never commit, log, or paste real user data: Takeout archives, `.mbox`/`.eml`
  mail, browser `History`/`Places` databases, real usernames, real email
  addresses, or anything from real `output/` runs. `inputs/usernames.txt` and
  `output/` are git-ignored and must stay untracked.
- Test fixtures are synthetic and must stay synthetic (invented usernames,
  `example`-style domains, hand-written temp mail bytes). Never make fixtures
  "more realistic" by adding personal, banking, or statement records — the tool
  does not process financial statements at all, and none belong here.
- Keep the offline-by-default property: anything touching the network lives
  behind the explicit `--online-usernames` flag and writes
  `ONLINE_SCAN_WARNING.txt`. Keep the guards that make this safe:
  `SAFE_USERNAME_RE` rejects unsafe usernames, only *user-supplied* usernames
  (never mail-inferred ones) are sent, and `build_maigret_cmd` passes them after
  a `--` separator.
- Do not persist secrets, API keys, cookies, or passwords anywhere in this
  product — it is designed to operate without ever receiving them.

## Code conventions

- All logic lives in the single leaf module `privacy_audit/main.py`. Keep it
  that way: no persistent on-disk state besides the requested `output/` files,
  no GUI, and no network path except the Maigret opt-in. Deterministic
  parsing and classification stay pure functions, importable without side
  effects.
- Failures are non-fatal by design: they flow through `warn()` into
  `WARNINGS`, then into `warnings.txt`, the console, and `summary.json`. A
  crash is a bug and so is a silently swallowed warning.
- Regression-test before fixing a defect: add a test to the existing
  `unittest.TestCase` classes in `tests/test_core.py` in the same style
  (temp directories, synthetic inputs, stubbed `run_maigret`) and watch it
  fail before the fix.
- Known inconsistency (checked 2026-10-21): `APP_VERSION` in `privacy_audit/main.py`
  (`1.1.0`) is ahead of `pyproject.toml`'s `version` (`1.0.0`). Fold the
  versions together when you next touch either.
