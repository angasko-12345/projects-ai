# Lessons — Cline sessions

Agent-scoped lessons. The canonical dated lessons live in
`.agents/memory/lessons.md`; those win on conflict. These are the ones worth
carrying forward when working from a Cline session, mostly about process and
tooling rather than about product code.

### 2026-10-04 - Offscreen Qt tests have two hang modes that look like "stuck"

- **Symptom:** A Qt test module passed 11 tests one at a time, then hung forever
  under `unittest discover` with no output and no failure.
- **Root cause:** Two invariants Tk never imposed. (1) A `QApplication` must exist
  before any `QWidget`/`QObject` is constructed — the shell was built before one did.
  (2) A modal dialog does not return until a human answers, and `closeEvent` raised
  `QMessageBox.question` when the teardown found an operation still flagged active.
- **Solution:** Call the app factory first in `setUp` and in the shared context
  builder; poll with `wait_until` rather than sleeping, because controller reads
  arrive asynchronously; drive the fake controller through a real `thread-finished`
  so nothing is left active; patch the confirm dialog in teardown so a future
  regression cannot wedge the entire suite rather than one test.
- **Remember:** A GUI test that *hangs* instead of failing is almost always a modal
  dialog or a missing application object, not your logic. Any test touching an
  operation lifecycle must complete it or stub the confirmation.

### 2026-10-04 - Redirecting APPDATA to isolate a test uninstalls the dependency

- **Symptom:** A launch check printed `ERROR: No module named 'PySide6'` and exited,
  one command after `import PySide6` had succeeded in the same shell.
- **Root cause:** My own isolation. The check set `APPDATA` to a temp dir, and on
  Windows `APPDATA` is the root of the per-user `site-packages`. Redirecting it to
  "avoid touching real settings" also removed every user-installed package.
- **Solution:** Confirm reality with `python -c "import sys, X; print(sys.executable,
  X.__file__)"`, then re-run without the override. Prefer explicit parameters
  (`make_context(..., settings_file=...)`) over mutating global env state.
- **Remember:** Before believing a missing-dependency error, prove it is an install
  fact and not an environment you changed. `HOME`, `APPDATA`, and `PYTHONPATH` have
  side effects far beyond configuration.

### 2026-10-04 - Editor chunked inserts corrupt a file you are building from scratch

- **Symptom:** Writing a ~380-line test module via repeated `insert_line` calls
  produced a file that spliced chunks into the middle of each other and failed to
  parse three times in a row (`SyntaxError`, then `IndentationError`, then more).
- **Root cause:** `insert_line` inserts *before* a line number, so appending to a
  growing file with a stale anchor lands mid-document. A blind
  "indent every line starting with `def `" fix then compounded the damage instead
  of locating the real class boundary.
- **Solution:** Wrote the file in explicit parts via a Python script
  (`path.write_text(part1); path.write_text(read + part2)`), asserting as it went,
  then ran the module to prove it parses. Afterwards, prefer the editor tool for
  *modifying* an existing file and chunked `Set-Content`/script writes for
  *creating* a large one.
- **Remember:** When a file you are assembling in chunks fails to parse, stop
  patching line-by-line and regenerate it deterministically. And do not fix
  indentation with a regex over the whole file — find the enclosing class first,
  because the same prefix means different things at module and class scope.

### 2026-10-04 - A remote can advance mid-task; prove the merge read-only first

- **Symptom:** An approved push was rejected as non-fast-forward — `origin/main` had
  advanced from `11a105c` to `1dc479d` during the task. The brief had forbidden
  `pull`/`reset`/`rebase`, so the approved action had become impossible.
- **Root cause:** A local branch diverges silently during a long task; nothing
  notices until the push fails. Constraints written for the start state go stale.
- **Solution:** `git fetch` (does not move `HEAD` or touch the tree) to see it;
  `git diff --stat HEAD...origin/main` to confirm the changes were disjoint;
  `git merge-tree --write-tree HEAD origin/main` as a conflict dry run. Then asked,
  and merged. Rebase was the wrong tool anyway: it rewrites the SHA and needs a
  clean tree, which would have forced stashing unrelated dirty work.
- **Remember:** Fetch before assuming the remote is where you left it; use
  `merge-tree` to prove a merge is clean instead of finding out halfway through.
  Never resolve divergence with `--force` on a shared branch — it destroys other
  people's commits irrecoverably. Here that one option would have deleted a
  colleague's `QWEN.md`.

### 2026-10-04 - Appending to memory files has an encoding/line-ending tax

- **Symptom:** `Add-Content -Encoding UTF8` joined the new section directly onto the
  previous line (files without a trailing newline), and the appended block arrived
  as LF into a CRLF file — then a cleanup pass "normalized" `project.md` to CRLF and
  made it the odd one out, since that file is LF.
- **Root cause:** These files are not uniform, and each differs: `decisions.md` is
  CRLF, `project.md` and `lessons.md` are LF. A global newline pass is wrong for at
  least one of them every time.
- **Solution:** After any memory write, verify in bytes: BOM count, `crlf` vs
  bare-`lf` counts, and a `repr()` of the seam. Convert only the appended range,
  matched on a unique marker, never the whole file.
- **Remember:** Memory files here are hand-maintained evidence with mixed
  conventions. Check the seam and the encoding rather than assuming a successful
  append produced a well-formed section, and never run a repo-wide newline
  normalization over them.

### 2026-10-01 - A green suite is not evidence a defect is fixed

- **Symptom:** The historical audit claimed the review-gate defect was
  "mutation-proven invisible to the suite": replacing the CLI readiness
  calculation with `ready = True` left the whole suite passing. That is
  simultaneously a warning and an invitation — it means the suite had no test
  standing on that line at all.
- **Root cause:** The tested path and the broken path were different paths. The
  standard flow was covered; the CLI custom-DAG path was not, because exercising
  it needed real worktree and git setup that no test did.
- **Solution:** Reproduce the defect in isolation first, then write tests that
  fail against the unfixed code, then fix. For path-divergence defects, add a
  cheap structural assertion (e.g. asserting the CLI module no longer contains
  the old formula) *in addition to* behavioural tests — the structural one
  cannot be satisfied by accident through an unrelated code path.
- **Remember:** Before trusting a passing suite on a reported bug, check whether
  any test would notice if the fix were deleted. Deleting the fix is the test.
  A regression suite that has never been seen failing is an assumption.

### 2026-10-01 - "Do not assume the description is still accurate" is doing real work

- **Symptom:** The task supplied four "known leads to verify", each stated
  confidently enough to be acted on directly, and warned specifically not to
  reintroduce four UGA fixes from earlier audits.
- **Root cause:** Audit reports describe a codebase at a point in time; this is
  a shared, actively-edited repository. In this case all four AgentOps leads
  turned out to be genuinely live in current source, and the four warned-about
  UGA items were out of scope and untouched — but that was only knowable by
  looking.
- **Solution:** Reproduce each lead with a small throwaway script before writing
  any production code. Three of the four repros needed fixes first (a wrong
  `create_workflow` signature, a wrong enum member, a foreign-key constraint
  from fake IDs) — which is itself the argument: the reproduction is where you
  find out that your mental model of the API is wrong.
- **Remember:** Treat a bug report as a hypothesis with a file:line, not a
  ticket. Reproducing first also prevents the specific failure mode of "fixing"
  code that was already correct and thereby breaking it.

### 2026-10-01 - Report what you rejected, not just what you did

- **Symptom:** The task asked for an explicit list of historical findings
  rejected as stale, and named four UGA fixes that must not be blindly
  reintroduced.
- **Root cause:** Silent scope decisions are indistinguishable from silent
  omissions to the next session. A future agent who cannot tell whether ROOT-015
  was checked-and-fine or simply forgotten will re-investigate it or, worse,
  trust it.
- **Solution:** Record rejected findings by name with the reason, and record
  deliberate non-changes in the code and in memory, not just in chat.
- **Remember:** "I did not touch X" is a finding. Write it where it will be
  read. This also protects against the opposite failure — a future session
  "fixing" something that was intentionally left alone, like `retry_merge`
  gating, which is now tracked as D9 in the roadmap.

### 2026-10-01 - Leave the dirty tree alone, and back it up first

- **Symptom:** The repo had pre-existing uncommitted changes (a deleted
  tokenizer file, an untracked HTML file) before any work began. Mid-task,
  another session began modifying five `universal-game-agent/` files.
- **Root cause:** This is a shared working tree under a single-writer rule that
  is easy to state and easy to violate. A convenient `git add -A` would have
  swept someone else's half-finished work into a commit.
- **Solution:** Back up `git diff` plus untracked files to `%TEMP%` before
  starting; stage by explicit path list; verify `git show --stat` before
  considering a commit complete.
- **Remember:** The single-writer rule protects against concurrency damage, but
  staging discipline is what actually enforces it. When you find unexplained
  modifications in `git status`, leave them and say so — that is a signal worth
  surfacing, not noise to clean up.