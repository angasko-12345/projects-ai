# Lessons — Hermes sessions

Hermes-relevant lessons. The canonical dated lessons stay in
`.agents/memory/lessons.md`; this file holds only the ones that are specific to
driving this repository from Hermes.

When one of these belongs to everyone, promote it into
`.agents/memory/lessons.md` rather than leaving it here.

## 2026-10-04 — Verify against the tree you were handed, not the one you remember

The task brief named a baseline of "97 tests, all passing". Running it produced
two failures. The tempting read was "pre-existing environment failures, move on";
the useful read was to `git status` and re-run the suite on the untouched tree
*before* editing, which reproduced both failures exactly and made "pre-existing"
evidence rather than an excuse.

Afterwards the total was 109 passed / 2 failed — still those same two. Reporting
"baseline green" required that pre-run, and without it the claim would have been
a guess dressed as a fact.

**Remember:** capture the baseline first, in the same session, before the first
edit. Re-derive anything inherited; three sessions in a row once accepted a
"known baseline failure" that was really a deleted tracked file.

## 2026-10-04 — A readability bound belongs on the unit the user sees

Implementing `MIN_CUE_SEC` per *word* broke the end-to-end pipeline: a 32-word
narration over a 6s tone burst demanded 12.8s and raised. The bound was not
wrong, the unit was — a viewer never sees a single word, they see a grouped
caption, and the pipeline always groups three words per cue.

The fix moved the allocation to per-caption (`max_words=3` threaded through) and
routed timing and grouping through one shared `_group_words` so the two could
not disagree about where a caption ends.

**Remember:** when adding a bound, ask which artifact the constraint describes —
the intermediate or the rendered one. Bounds on intermediates silently multiply
downstream.

## 2026-10-04 — Iterative clamp-and-rescale allocation is unstable

First attempt at normalizing weights under a floor and a cap: clamp anything out
of bounds, subtract it from the budget, re-scale the survivors, repeat. This
does not converge — a cue clamped up to the floor makes the survivors' shares
*larger*, so more get clamped, and the budget walks off a cliff. It failed five
tests on the first run, including negative cue durations.

Correct approach: the clamped sum `Σ clamp(k·wᵢ, MIN, MAX)` is monotone in the
scale factor `k`, so **bisect** for `k` (80 iterations) and place the residual in
the cue with the most headroom.

**Remember:** for constrained proportional allocation, treat it as a monotone
root-find, not a loop. If the constraint is "clamp to a range," the clamped sum is
monotone and bisection is the boring correct answer.

## 2026-10-04 — Shared dirty trees need hunk-level staging

`.agents/memory/decisions.md` had unstaged edits from a concurrent agentops
session plus mine. `git add <file>` would have committed both.

Attempted and failed, in order: `git apply --cached` with a hand-built patch
(does not apply, because the index already differed from HEAD), then
`git update-index --cacheinfo` with an LF-normalized blob (produced a 442-line
diff, because the file is stored CRLF and normalizing rewrote every line).

What worked: build the desired blob as **HEAD bytes + only my appended block**,
preserving the file's existing CRLF ending, then
`hash-object -w --stdin` and `update-index --cacheinfo <mode> <sha> <path>`.
Result: 7 lines staged, the other session's hunk untouched in the working tree.

**Remember:** for one-file commits in a shared tree, construct the index blob
from HEAD rather than from the working tree, and preserve the file's existing
line endings. Never `git add .` / `-A`. Verify with
`git diff --cached --stat` showing only your lines, then `git status --short`
afterwards to confirm the other session's edits are still unstaged.

## 2026-10-04 — Regex alternation order silently drops cases

`(\.{2,}|…|[,;:!?]+)$` never matched a word ending in a single `.` — the `.` was
absent from all three branches. Sentence-final punctuation therefore earned *zero*
pause weight while a comma earned 0.5, which is backwards from the intent.

Found only because a test asserted `durations["waves."]` and raised `KeyError`
rather than a soft failure. Adding `.` inside the character class fixed it.

**Remember:** when a regex has a multi-char alternative alongside a character
class, check the class contains every single-character case, and prefer writing
the test that keys on the exact literal token so a missing match raises loudly.

## 2026-10-04 — A test fixture can encode the exact bug a new gate hunts

**Symptom:** a newly added narration-coherence gate immediately failed
`test_end_to_end_produces_vertical_video`, which had stubbed TTS with a
**6-second tone for a 32-word script** — about 5.3 words/second, physically
impossible speech.

**Root cause:** the fixture was written before the gate existed, and nothing had
ever checked that its fake audio matched its own script. The gate did exactly
what it was built to do; the fixture was the defect.

**Fix:** derive the stub tone's length from the text rather than hard-coding it,
and follow the change through every assertion that depended on the old value
(the video and metadata duration checks both needed updating).

**Remember:** when a validation gate rejects a long-standing test, read the
fixture before touching the gate — a fixture asserting something impossible is
a bug report about the fixture. Then grep the file for the old hard-coded value;
it usually appears in more assertions than the failure names.

## 2026-10-04 — A truncated test run is not a result

**Symptom:** a full suite run hit a `timeout` mid-way, printed no summary, and I
reported "4 failures" from the partial progress line. One of those was my own
broken assertion, which I only found afterwards by grepping the test file.

**Root cause:** a `-q` run piped through `tail` loses everything when the timeout
kills it, and a progress line of dots and `F`s has no names attached. Counting
`F` characters from it is guesswork, and I reported it as observation.

**Fix:** redirect the full run to a file, read the `short test summary` section
back after it exits, and check the exit code. Also kill any run already in
flight before starting the corrected one — otherwise the second run silently
tests pre-fix code and its numbers are about code that no longer exists.

**Remember:** never characterize pass/fail from output you did not see to the
end. If the summary did not print, say the run was truncated and get real names
before telling anyone anything about failures.

## 2026-10-05 — Call the new function once, directly, before running any test

Six failed attempts on one AgentOps fix, and the decisive one was a guard I
wrote myself: `_is_git_working_tree` called `subprocess.run(...)` and
`sys.platform` **without importing either name**. It raised, the `except`
returned `False`, and the guard silently disabled itself. The test it was
written for passed anyway, so the fix looked correct because it never ran. Found
it by calling the function in a REPL and noticing `w.subprocess` had no
attribute at all.

The same failure shape appeared three times in one session: a substring match
against the wrong argv index (returned "not applicable", feature inert), and a
harness that set a field on the wrong dataclass (`AttributeError`).

**Remember:** after writing a patch, call the new or edited function once with an
input whose answer you already know. Then call it with the boundary case
(`git repo + changed file`, `git repo + clean tree`, `plain directory`). Two
calls — positive then boundary. Not a test: a test's failure message points at
the assertion, not at the line that raised, and a broad `except` hides the
NameError entirely. This is cheaper than any review process.

## 2026-10-05 — A repair loop needs a demonstrated failure, not an absent signal

Added an `UNVERIFIED` verification status so "no applicable evidence" would stop
reading as "a check failed". The report was right, the task was right — and then
`run_high_level` collapsed it one layer down: any non-PASSED verification with a
passed implementation entered the repair loop and invented *"Repair the
configured verification failure."* for a failure no check had demonstrated, which
then invites an agent to repair code that was never shown broken. Luna found
this by reading `run_high_level`, not by running it.

**Remember:** adding a state is the easy half. The rule that generalises —

> A new state is not implemented until its entire downstream path has been
> tested for semantic preservation.

Walk every place that branches on the old states and ask what it now does with
the new one. The symptom to expect is a *behaviour invented for the new state*:
an absent signal routed into a handler written for a real defect.

## 2026-10-05 — An audit with a stopping condition may legitimately find nothing

Auditing five success boundaries, I found real defects on four of them and the
fifth was already hardened. The pull to keep going was strong precisely because
four hits in a row made a fifth feel mandatory. Reporting "clean" for one
boundary is a result, not a gap — and it should name the guard that hardens it,
so the next reader sees the boundary was checked rather than skipped.

**Remember:** state the stopping condition before starting, write
`producer → evidence → decision-maker → negative case` per boundary, and when
you can, stop and say so. An audit that returns five findings because looking was
cheap has failed at the part that mattered. The user's own instruction was the
sharpest version of this: *"a clean boundary is a valid result — report it, do
not manufacture a finding."*

## 2026-10-05 — Reproduce a reviewer's code-level claim before fixing it

Luna's audit asserted that `run_high_level` routes UNVERIFIED verification into
the repair path. I could have patched and reported. Reproducing first took one
script and *confirmed* it exactly:

```
implementation  passed
verification    blocked
debugging       passed  'Repair the configured verification failure…'
```

Her report is a hypothesis like any other, and the reproduction is often the
most informative step — it can show the mechanism differs from the one
described. Cheap, and it converts their claim into your evidence.

## 2026-10-05 — Local green is not CI green: platform skips hide half the suite

CI reported 15 failures on `agentops` that did not exist locally. Reproduced on a
pristine worktree at `origin/main`: exactly 15, all in `test_workflow.py`, all
mine.

Cause: those tests ran a workflow in `Path.cwd()` and asserted READY, but wrote
no files. Since `dcaab06` an implementation task must change the working tree, so
on a clean checkout the collector found nothing -> `no_evidence` ->
implementation blocked -> never READY. They passed locally only because 24
uncommitted files were lying around, and **those files supplied the evidence for
tests that produce no work.** An empty `_probe.tmp` flips a clean tree from
`READY: False` to `READY: True`.

**Remember:** a test harness must declare its own evidence — inject a metadata
collector rather than reading the working tree. Then verify on a *pristine
worktree*, not just in the live tree: `git worktree add --detach <tmp>
origin/main`, copy the fix in, run there. My live tree was the thing under test.

Second, independent gap: **CI runs ubuntu + Python 3.11, so ~45 POSIX-only tests
run there that Windows skips** (`skipIf(os.name == "nt")` on symlink permissions,
`killpg`, process groups). Local green therefore proves nothing about CI. Run
both `python -m unittest discover -s tests` (CI's command) and `pytest`, and
remember pytest and unittest collect differently — a suite can be green under one
and red under the other.

Worst part: CI was the only thing checking my work in a clean environment, and I
reported "545 passed" for hours while `main` had 15 known failures. **A number
reported about the live tree is not a number about the shipping condition.**

