# PR #21 — least-privilege Codex args and no config shadowing

**Merged:** 2026-10-20, merge commit `4d1c9333` (merge commit, deliberately not a
squash). Branch `fix/codex-args-paths-shadowing`.

## What shipped

| File | Change |
|---|---|
| `agentops/agentops/agents/agents.yaml` | Codex args change from `exec --approve-for-me {prompt}` to `exec --sandbox workspace-write {prompt}` |
| `agentops/agentops/paths.py` | `resource_path` validates a preferred filesystem candidate for the bundled config (`_is_bundled_config`): a directory, an empty file, or an unparseable file can no longer shadow the packaged `agents.yaml` |
| `agentops/README.md` | Documents the workspace-write trade-off and `--approve-for-me` as an explicit opt-in |
| `agentops/tests/test_config_registry.py` | `test_bundled_codex_args_use_only_known_flags`: allowlisted flag set, asserts `--approve-for-me` absent and the exact argv tuple |
| `agentops/tests/test_bundle_paths.py` | `ConfigShadowingTests`: directory / empty-file / stale-unparseable decoys all fall through to the real bundle |
| `.agents/evidence/verification.json` | Regenerated from real runs, not hand-edited |

`--approve-for-me` was never an invalid flag: `codex-cli 0.163.0` (installed on
this box) documents it as "Route approval requests through automatic review
using the workspace-write sandbox". It was removed for least privilege, which
is the correction this PR carries over its own earlier rationale.

## Verification (Windows, Python 3.14.7)

- Suite at the final head `c34f4d7`: `cd agentops && python -m unittest discover -s tests`
  → **831 tests, OK (skipped=4)**, exit 0, 148.5 s, clean tree. Main was 827, so
  the +4 are exactly the tests added here; skips stayed at 4.
- The four new tests run individually: `python -m unittest tests.test_bundle_paths
  tests.test_config_registry -v` → 38 tests OK, 0 skips, all four `ok`.
- Evidence: `python tools/evidence/generate.py` per product, each in its own clean
  clone of `ac7cae9` — agentops 831 / 4 skipped, universal-game-agent 498 / 1,
  mini-llm 252 / 1, ai-token-tracker 52 / 0, all `pass`,
  `clean_checkout: true`. `tools/evidence/check.py` exit 0 at `c34f4d7` and again
  on `main` at `4d1c933`.
- CI on `c34f4d7`: `tests`, `test`, `check` ×2 all pass; `mergeStateStatus` moved
  from `DIRTY`/`CONFLICTING` to `CLEAN`.
- `git diff 4d1c933 c34f4d7` is empty, so the tree that landed is byte-identical
  to the tree that was tested.

## SHAs

- Base `main` before the sync: `c825a1b`
- Branch head before: `6b61d9e` (based on `dca259e`, conflicting)
- Sync merge: `ac7cae9`
- Final head (evidence commit): `c34f4d7`
- Merge into `main`: `4d1c933`

## Follow-ups (non-blocking, recorded in the live queue)

1. No test pins the *positive* preference path — a real, parseable file at
   `agents/agents.yaml` must still win over the packaged copy. Today that is only
   covered indirectly by every test that calls `load_config()`.
2. `_is_bundled_config` validates with `json.loads` only, while `_load_data`
   accepts JSON-valid YAML *or* conventional YAML when PyYAML is installed. A
   conventional-YAML bundle at the preferred path therefore falls through to
   `importlib.resources`, which resolves the same file in a source checkout — no
   breakage, but the fast path and the loader disagree about what "valid" means.
3. Operational: the evidence records `ac7cae9`. A squash merge would strand it
   and `check.py` would fail with "the history it described is gone" — use a
   merge commit, or regenerate evidence on `main` after squashing.
