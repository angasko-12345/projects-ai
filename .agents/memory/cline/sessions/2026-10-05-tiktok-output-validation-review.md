# Session record - 2026-10-05 tiktok-slop-factory output-validation review

Read-only review of commit `b4b9514` ("Add final-output validation to video
generation pipeline"). Verdict: **FAIL**. Nothing was modified, staged, committed,
or pushed; `tiktok-slop-factory/` is byte-identical to its pre-review state.

Full findings are in `.agents/memory/decisions.md` (entry dated 2026-10-05) and
state in `project.md`. This record holds only what is specific to reviewing from a
Cline session in this tree.

## The finding that mattered most was invisible in this working tree

`b4b9514` committed `from . import gemini, providers, ...` into `app/pipeline.py`
while `app/providers.py` stayed **untracked**. In this working tree every command
looked fine, because the untracked file satisfied the import. Only a clean export
of `HEAD` exposed it:

```
git archive --format=zip -o head.zip HEAD tiktok-slop-factory
Expand-Archive head.zip x
python -c "import app.pipeline"   # ImportError: cannot import name 'providers'
python -m pytest --collect-only -q # 83 collected, 3 errors
```

The new test file was itself one of the three collection errors. **When reviewing
a commit in a tree that has untracked files, verify the commit from an export, not
from the checkout.** A green local run is evidence about the working tree, not
about the commit.

## Headline test count was a collection count

The prior report said "24 focused tests". `--collect-only` confirms 24 collected,
but the suite is **21 passed, 3 failed**. The three failures were the pipeline-level
tests, and they were red because a stub narration contradicted a stubbed duration
and tripped an *earlier* gate (`_assert_narration_coherent`, added in ancestor
`f31221f`) before reaching the code under test.

Both the collection count and the pass count are worth quoting, and they are not
the same number.

## Reproduce the claim before reviewing the code

The commit claimed truncation detection. Reading `validate_final_output` showed it
compares `probe_duration()` - a `format=duration` **container header** read - against
the narration length. A throwaway script settled it in one shot:

```python
# 10s faststart MP4, keep the first third of the bytes
t.write_bytes(f.read_bytes()[:len(f.read_bytes())//3])
renderer.probe_duration(t)   # -> 10.0   (header intact)
renderer.validate_final_output(t, min_duration=10.0)  # -> returns 10.0, no raise
```

Because the renderer mandates `-movflags +faststart`, `moov` is at the front, so a
tail truncation leaves the header fully intact. The check passes a file cut to a
third of its size. Ten minutes of reading would not have made this as concrete as
one script.

## Suite runtime shapes the tooling, not the other way round

`python -m pytest` in `tiktok-slop-factory/` takes **~594s**, dominated by real
1080x1920 renders (`test_end_to_end_produces_vertical_video` alone is ~527s, and it
is memory-hungry enough to fail with `Cannot allocate memory` when the suite runs
alongside other python processes). Background it and poll; see
`../agentops-verification-workflow.md` for the pattern, which transfers to pytest.

One failure in the full run - `test_pipeline.py::test_end_to_end_produces_vertical_video`,
`FFmpeg failed: ... Cannot allocate memory` - was **environmental, not the change
under review**. Re-running it in isolation passed. Report that distinction rather
than folding it into the verdict; the reviewed commit did not cause it.

## Review posture used

- Verified each of the nine claims individually against the code, and marked which
  ones the tests genuinely support. Seven hold, claim 7 (truncation) does not work,
  and claims 8 and 9 are correct in logic but have **no working test evidence** -
  their only tests are red and additionally mock the validator itself.
- Checked for bypasses repo-wide and found none worth reporting as a defect: the
  single production caller of `render_video` is the one that validates. Stating a
  clean result is as useful as stating a defect.
- Removed the scratch directory used for the clean export and confirmed `git status`
  matched its pre-review state. Another session was actively writing
  (`agentops/`, `small-projects/mini-llm/`, `universal-game-agent/` all moved during
  the review), which is the concurrent-writer hazard in `../environment.md`.
