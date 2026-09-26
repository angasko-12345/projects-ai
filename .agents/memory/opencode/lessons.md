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

## Cross-references

- `../lessons.md` — canonical dated lessons, including the same 2026-09-26 entries in full.
- `../decisions.md` — the five decisions from the 2026-09-26 session.
- `../architecture.md` — the instruction hierarchy and memory-folder layout.
