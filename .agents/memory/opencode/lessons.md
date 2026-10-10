# Lessons — OpenCode sessions in this repo

The canonical dated lessons live in `../lessons.md` and are append-only. This file records
only what is specific to working from an OpenCode session, plus pointers. It does not
duplicate entries — see `../lessons.md` for the full record.

## 2026-09-26 — repository reorganization and instruction hierarchy

**What was done.** Audited the repo (two independent products, history clean, no rewrite
justified), backed it up, reclaimed 19.71 MB of regenerable artifacts, fixed a checkpoint
ignore gap that risked staging ~16.3 MB of binaries, committed `.agents/skills/`, and built
a layered agent-instruction hierarchy (`.agents/AGENTS.md` universal, plus
`agentops/AGENTS.md` and `universal-game-agent/AGENTS.md`).

Full record: `sessions/2026-09-26-repo-reorganization.md`.

### The lesson that matters most

**Writing a plausible claim about code is a failure mode, not a typo.** The first draft of
the instruction hierarchy carried seven factually wrong claims and one entirely invented
defect. Every one looked reasonable. They were caught only by an independent read-only
review, then re-verified by grepping for the removed strings.

Concretely, what was wrong: a test count off by 3; "four `ppo_final.pt` files" when two
exist; "a 12-line `ppo:` tail byte-identical across all five YAMLs" when 9 keys match and 5
differ; a directory described as present *after it had been deleted*; a Windows spawn helper
attributed to `runner.py`, which contains none (the owner is `runtime.py`); a CWD-fragility
claim that was inverted; DTOs attributed to the wrong modules; and a claimed defect in
`.agents/AGENTS.md` that file does not have.

**Apply it like this:** read the code before writing the sentence. Do not describe a defect
in a file you have not opened this session. If a fact cannot be checked, mark it unverified
instead of asserting it.

### A subagent will push back on a wrong premise — let it

The reviewer's own spec contained an error: it claimed `.agents/AGENTS.md:64` held a Tk
`root.after` rule, when line 64 is the leaf-modules rule. The fixer, told to apply all
corrections, scoped the two rules that genuinely exist and explicitly declined to invent a
third to match. That was the right call and it caught a second-generation error. Give
specialists the authority to reject a spec item rather than forcing compliance.

### Governance files are uniquely load-bearing

An instruction file that is wrong is worse than one that is silent, because it carries
authority. Every future agent reads it as fact and builds on it. This raises the bar for
writing one well above the bar for writing ordinary prose.

## 2026-09-26 — working alongside a parallel writer

Another agent was actively committing to this repository and editing
`universal-game-agent/` throughout the session. Four commits landed mid-task, a 4.1 MB
checkpoint appeared with no config reference, deleted `__pycache__` directories were
regenerated, and 7 source files were left modified at the end.

What this required, and what to keep doing:

- Re-check `git log` and `git status` immediately before every write batch **and** again
  before committing. Assumptions from the start of the session do not hold at the end.
- Stage **explicit paths only**. Never `git add -A` in a tree with large untracked binaries.
- Verify `git diff --cached --name-only` right before committing, so a concurrent `git add`
  by another agent cannot get swept into your commit. Use `git commit -- <paths>` when the
  index may not be clean.
- Treat running the test suite as a **write** operation, not a read.
- Assume any review you requested is describing a tree that has since moved, and re-check
  its most load-bearing findings before acting on them.

## 2026-09-26 — verification steps that produce false positives

A contradiction sweep reported that the obsolete rule "Pi is the sole writer" still existed
in two files. Both hits were the *new* rule — "Either Pi or Oh-My-Pi is the sole writer" —
because the search pattern matched as a substring of its own replacement.

When sweeping for "old text still present", read the matched lines rather than trusting the
count, and anchor patterns so a correct replacement cannot match. A verifier that cries wolf
gets ignored exactly when it matters.

## 2026-10-02 — UGA bug-root implementation and experiment validity

Full record: `sessions/2026-10-02-uga-root-014-036.md`.

### Review the measurement contract before writing it

**The ROOT-036 design was reviewed by an independent read-only reviewer before any code was
written, and that review earned its keep.** It returned **six failure modes in a proposal that
already looked careful**.

Three would have broken the test suite. Two were **unlisted consumers the proposal had missed**:
fake report dicts in `tests/test_cli.py` that carry no `reward_semantics`, and an exact-key-set
assertion in `tests/test_eval.py`.

One would have **reintroduced the precise lie the change existed to remove**: an `__init__`
snapshot of `reward_semantics` would have kept publishing hit counts after the reward provider was
swapped. A stale value is worse than no value when the artifact is evidence — it looks measured.

Its conclusions were adopted: declare on the `RewardProvider` ABC, property rather than snapshot,
downgrade on `skip > 1`, derive comparison metrics instead of hand-maintaining a second list.

### Absence, not zero, for "this was not measured"

**`0.0` hits reads as "hit nothing"** — to a human and to any plotting script. The eval report now
**omits the hit/miss keys entirely** for non-sign rewards rather than reporting zero.

This applies to any metric that is not measurable under a given configuration. If the number was
never computed, it must not occupy the slot where a computed number belongs.

### Test fixtures carry the contract too

**A fixture that deliberately does not satisfy a new contract is still evidence.** `ScriptedEvalEnv`
in `tests/test_eval.py` intentionally did **not** declare sign semantics, so the existing hit/miss
assertions had to move to a `SignScriptedEvalEnv` subclass. The pre-existing tests were **updated
rather than made to pass** by loosening the fixture.

The fake report dicts in `tests/test_cli.py` were two more fixtures that had to gain the new
field. When a contract lands, the tests are consumers too, and every fixture is a claim about the
contract.

### Verify your own harness before blaming the product

The first external-Pong pre-flight reported **0 hits and 23 misses while red was present in the hit
band for 15 steps**. That is a contradiction, so the harness got the suspicion first — and it was
the right instinct.

**Two separate harness bugs were found and fixed:**

- The ball detector **averaged the white Windows title bar**, because the capture is the whole
  window rect, so the analysis had to be cropped to the black canvas.
- **The run was too short.**

Red-on-latch only happens on a real hit, so "red present but zero paid hits" was a **real signal,
not noise**. After the fix the bands behaved correctly: 0 steps in the ambiguous 200..300 gap, and
the MISS band (`>=300`) matched the miss count exactly. The extra run was worth it.

### Ask before starting a long run on a machine the user is using

A **~45-90 minute GUI experiment** was dispatched to a background lane while the user was actively
at the machine. It sends **real `SendInput` keystrokes and opens real windows for three phases**.
The user cancelled it.

Ask first. And prefer a **bounded pre-flight that proves the measurement precondition** over a long
run that cannot be interpreted until the precondition holds — the 240-step pre-flight answered the
detector question in minutes; 4096 timesteps plus two 100-episode evals would not have answered it
any faster.

## 2026-10-10 - mini-llm scaling (Stages 1-4 and chunked loss)

Full record: `../mini-llm/sessions/2026-10-10-scaling-stages.md`. The canonical decisions and
the dateable lessons are in `../decisions.md` and `../lessons.md`; this note adds only what was
specific to driving the work from OpenCode.

- Running the suite is a write. Every run created `__pycache__` across
  `small-projects/mini-llm/`; budget the cleanup and do not run it while another writer owns
  that tree.
- The chunked-loss peak-memory benchmark lived in the pre-approved scratch dir
  (`C:\Users\admin\AppData\Local\Temp\opencode\bench_chunk.py`), not in the repo, per the task's
  no-scratch-files rule. It reads `WorkingSetSize` through `kernel32.K32GetProcessMemoryInfo`
  (the `psapi` binding returns 0; see canonical lesson).
- `C:` filled to 0 bytes mid-session and made the suite fail wholesale with `[Errno 28]`. The
  run was rerouted with `TEMP`/`TMP` set to `D:\tmp\mini-llm`. This is a shared machine: do not
  delete another agent's temp to make room.
- `rtk git -C <repo> ...` worked for log/diff/status. `rtk` has no `cat`/`ls`/`rg` on this box
  (it falls back to a missing binary and fails), so file reads stayed on the Read tool and
  content searches on `Select-String`.

## Cross-references

- `../lessons.md` — canonical dated lessons, including the same 2026-09-26 entries in full.
- `../decisions.md` — the five decisions from the 2026-09-26 session.
- `../architecture.md` — the instruction hierarchy and memory-folder layout.
