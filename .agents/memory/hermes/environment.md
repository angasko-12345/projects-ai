# Environment — verified facts (Hermes sessions)

Every line below was checked on this host. Anything inferred is marked.
Re-verify before relying on it; toolchains move.

## Verified 2026-10-04

Checked while adding speech-aware caption timing to `tiktok-slop-factory/`
(commit `58d50c2`).

- `python 3.14.7`, `git 2.55.0.windows.5`, `pytest` 8.x, `uv` installed.
  `python3` is **not** on PATH; use `python`.
- Host shell is **git-bash/MSYS**, not PowerShell or cmd. Use `ls`, `grep`,
  `$HOME`, `&&`, single quotes. PowerShell builtins silently fail here.
- MSYS path translation is **disabled** for native programs. `git -C /c/Users/x`
  fails; pass `C:/Users/x` forward-slash paths instead. `cd /c/Users/x` works
  because `cd` is a bash builtin.

## Trap: `write_file` refuses a partially-read file

Reading a file with `offset`/`limit` records it as a *partial view*.
`write_file` then refuses to overwrite it, repeatedly, with the same argument —
so retrying the identical call never succeeds.

**Use `patch` for an existing file.** A single `patch` with a large
`old_string`/`new_string` replaces whole functions and sections cleanly. Only
reach for `write_file` on a file you created in the same session, or one you have
read in full in a single call.

## Trap: `execute_code` writes must be bytes

`subprocess.run(..., input=data, text=True)` raises
`TypeError: write() argument must be str, not bytes` inside the writer thread,
which surfaces as a hung cell and can eat the full five-minute budget.

Pass bytes for `input` and leave `text=True` off, then `.decode()` the output:

```python
sha = subprocess.run(["git", "hash-object", "-w", "--stdin"],
                     input=new_bytes, capture_output=True).stdout.decode().strip()
```

Related: `git update-index --cacheinfo` accepts either
`"100644,<sha>,<path>"` or three separate arguments
`["--cacheinfo", "100644", sha, path]`. The comma form silently fails with
`expects <mode>,<sha1>,<path>` when passed as one shell-quoted string.

## Trap: full product suites exceed the 300s execute_code budget

`tiktok-slop-factory` takes **~630s** for `python -m pytest tests -q` (it was
~300s before the narration-gate pass; the end-to-end test now renders ~22s
videos instead of 6s). It does not fit in `execute_code`'s five-minute budget,
and it does not fit under a foreground `terminal` timeout either.

Run it in the background with the output redirected to a file, then read the
`short test summary` section back after it exits:

```
python -m pytest tests -q > <scratch>/full.txt 2>&1; echo "EXIT=$?" >> <scratch>/full.txt
```

**Do not** pipe it through `tail` and infer results from the progress line. A
`-q` progress line is dots and `F` characters with no names attached, and a
timeout kills it before any summary prints. Counting `F`s from it once led to
reporting "4 failures" when the real number was 3 and one of them was
self-inflicted. See `lessons.md`.

If a run is already in flight when you fix something, kill it before starting
the corrected one — otherwise the second run tests pre-fix code and its numbers
describe code that no longer exists.

`tiktok-slop-factory` is a **pytest** project, not unittest:
`python -m unittest discover -s tests` reports `Ran 0 tests` / `NO TESTS RAN` and
exits **5**. Do not treat the quiet output as a pass — check the collected count
and the exit code together, because this command never runs a single test. (`agentops/` and `universal-game-agent/` *are* unittest projects.)

## Verified 2026-10-05 (AgentOps correctness session)

- `C:\Python314\python.exe` holds the only `torch` install on this host. The
  default `python` (3.14.7 in the Hermes venv) cannot see it, so six
  torch-gated `universal-game-agent` tests report `ModuleNotFoundError` under
  the default interpreter and pass under `C:\Python314\python.exe`. The suite
  is fine; the interpreter is wrong.
- `pip` installs into `C:\Python314`, but `python` resolves to the Hermes venv.
  Install with `python -m pip` and verify in the interpreter you will run with.
- AgentOps responds to both runners, and the counts legitimately differ:
  `python -m pytest tests -q` → **536 passed, 4 skipped** (41 subtests counted),
  `python -m unittest discover -s tests` → **Ran 540 tests, OK (skipped=4)**.
  Neither is wrong; subtests are counted differently. Name the runner whenever
  you quote a count, because "536 vs 540" otherwise reads as a discrepancy.
- ChatGPT is reachable for agent-to-agent work over CDP on `127.0.0.1:9333`
  against the user's already-logged-in Brave. `Input.insertText` acks can
  time out while the text has in fact landed — verify by reading
  `.ProseMirror` `innerText` length before assuming failure or resending.
  Chunk long messages; a 4k-character paste exceeds the daemon's ack window.

## CI runs a DIFFERENT environment than this host

`.github/workflows/agentops.yml` runs `ubuntu-latest` + Python 3.11, headless
(no `$DISPLAY`), command `python -m unittest discover -s tests` from `agentops/`.
`.github/CI.md` documents the policy.

Consequence for local verification: **~45 POSIX-only tests run in CI that Windows
skips** (`skipIf(os.name == "nt")` around symlink permissions, `killpg`,
POSIX process groups). Local green does not imply CI green, and the 4 local skips
are not the 49 CI skips — most of the CI skips are the documented headless-GUI
policy plus the POSIX/Windows split.

**Read CI output, never infer it.** `gh` is not installed and the run-logs API
returns 403 unauthenticated, so a red CI job cannot be diagnosed from this host
without the user pasting the failure output. The public runs/jobs endpoints do
work unauthenticated, so "which workflow failed and at which step" is knowable;
the test names inside are not.

**Always verify on a pristine worktree before claiming a fix works:**

```
git worktree add --detach <tmp-path> origin/main
# apply the change, then run the suite there
```

The live tree may be dirty in ways that make broken tests pass.

## Suite baselines for this repository

Run each from its own directory; there is no repository-wide test command.

| Product | Command | Baseline 2026-10-04 |
|---|---|---|
| `agentops/` | `python -m unittest discover -s tests` | 512 tests, 4 environment skips, OK |
| `universal-game-agent/` | `python -m unittest discover -s tests` | 349 tests, 1 skip, OK |
| `tiktok-slop-factory/` | `python -m pytest tests -q` | **125 passed, 2 failed** (628s; both need `GEMINI_API_KEY`/`.env`) |
| `small-projects/mini-llm/` | `python -m unittest discover -s tests` | 115 run, 1 skip, OK |

The two `tiktok-slop-factory` failures are
`tests/test_config.py::test_loads_dotenv_from_project_root` and
`tests/test_gemini_parsing.py::test_generate_ideas_rejects_duplicate_padding`.
Both are environmental — they raise `ConfigError: GEMINI_API_KEY not set` — and
both were reproduced on a clean tree before any edit. **Re-derive them before
calling any failure "pre-existing"**; see the dated lesson in `lessons.md` about
inherited baselines.