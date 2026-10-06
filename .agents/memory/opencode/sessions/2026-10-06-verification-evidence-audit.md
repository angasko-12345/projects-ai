# Session: verification-evidence audit

**Date:** 2026-10-06
**Scope:** read-only audit of the evidence mechanism built earlier the same day, then the
minimal fixes it found
**Outcome:** one real defect and one CI enforcement gap found and fixed; the same-commit
forgery limitation confirmed, accepted, and written down

## What was audited

The mechanism (`tools/evidence/`, `.agents/evidence/verification.json`) had been built and
committed earlier on 2026-10-06 (`9a73526`, `96bc2d5`, `cadb67f`). This session audited it
rather than extending it: same-commit forgery, whether the CI workflow actually protected
anything, whether the clean-clone guarantee was real, four staleness cases, remaining count
duplication, and whether the fixed AgentOps tests actually fail against the buggy code.

## Method

Everything was verified by running it, not by reading it. A throwaway
`git clone --no-hardlinks` of the repository at `cadb67f` in the scratch directory, then
committing synthetic histories inside it to produce each staleness case. Mutation testing
in scratch copies of `agentops/` for the regression tests. The working tree was never
mutated to find any of this out.

## Findings

**Defect - a partial regeneration re-dated stale numbers as current.** Reproduced: a
UGA-only commit, then `generate.py agentops`, then `check.py` reported OK while UGA's count
predated the commit. The document carried one `commit`; staleness compared that against HEAD
and, after a partial run, found nothing to diff. Fixed by giving each record its own commit
and judging per record.

**Enforcement gap - the checker did not run on the commits that make evidence stale.**
`agentops/**` was missing from `evidence.yml`'s trigger paths, verified by parsing the YAML.
A stale-evidence commit passed on the strength of three per-product workflows that never
read the evidence file. Fixed.

**Smaller - `record["directory"]` was never validated against `record["product"]`.** A record
could name a directory that never changes and carry a count describing code nobody looked
at. Now pinned to the product's real directory.

**Same-commit forgery: confirmed possible, accepted.** Change product code, edit the counts
and the recorded summary lines together, point `commit` at the new HEAD - `check.py` passes.
Closing it needs the suites re-run in the workflow or a signature; both are out of scope.
The forgery expires at the next product commit, and the limit is now stated in
`.github/CI.md` rather than implied by silence.

**Not defects, confirmed working.** The clean-clone guarantee is real: a failing test planted
only in the caller's dirty tree was invisible to the clone (624 tests, 0 failures), while
`--in-place` on the same tree saw it (625 tests, 1 failure) and set `clean_checkout: false`.
The three fixed AgentOps tests do fail against the buggy code - reverting the merge gate
turns `MergeGateBoundaryTests` cases B and C red, and only because the fixture now seeds a
PASSED implementation task and keeps the store open across the call.

## Verification

- `agentops/` `Ran 624 tests` - OK (skipped=4)
- `universal-game-agent/` `Ran 495 tests` - OK (skipped=1)
- `small-projects/mini-llm/` `Ran 146 tests` - OK (skipped=1)
- `tools/evidence` `Ran 38 tests` - OK (was 32)
- All three product figures measured in a clean clone of `4912a1d`, not in the dirty tree.

## Commits

`9365920` - per-record staleness, CI triggers on product commits, directory pinning,
`load()` removed, `--output` outside the repo fixed.

## Still open

Counts remain in agent-scoped memory that is not covered by the checker's scan
(`.agents/memory/architecture.md`, `.agents/memory/oh-my-pi/*`). Agent-scoped memory loses to
canonical memory under the hierarchy rule, and two of those files are another session's
dirty work. Worth a follow-up by whoever owns them.
