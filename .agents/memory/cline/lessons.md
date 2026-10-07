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

### 2026-10-06 - Read-only reboot audits: PowerShell quoting forces probe scripts to files

- **Symptom:** Inline `python -c` with nested quotes failed three times in a row
  (`Unexpected token ')'`, missing string terminator) when querying OMP
  `history.db`, Hermes `state.db`, and Brave `History` — the audit stalled on
  quoting, not on data.
- **Root cause:** PowerShell parses the command line before Python sees it, so
  `chr(39)` tricks and nested double quotes do not survive intact. SQLite
  `mode=ro` URIs and `datetime(..., 'unixepoch')` need single quotes that the
  shell eats.
- **Solution:** Write each probe to `%TEMP%` via the editor tool
  (`omp_hist.py`, `hermes_sess.py`, `brave_gpt2.py`), run `python <file>`, and
  for locked DBs (Brave `History`) copy to Temp first, query, delete. All
  probes open read-only (`mode=ro`); Temp scripts are scratch, never repo
  state. No repo file was touched by the probes.
- **Remember:** More than one statement or any single quote inside means a
  script file, not `-c`. And a locked SQLite file is read via copy-then-query,
  never by killing the owner.

### 2026-10-06 - PyInstaller onefile bootloader fails on ACL-restricted directories

- **Symptom:** `AgentOps.exe` (PyInstaller onefile) failed with
  `[PYI-ERROR] Could not create temporary directory!` when launched from
  `D:\admin\code\projects\agentops\dist\`, but worked fine from `D:\t` or
  `C:\tmp\exetest\`. Exit code -1, no `_MEI*` dir created, no log written.
- **Root cause:** The PyInstaller onefile bootloader tries to create its
  extraction temp directory *in the current working directory* first
  (`runtime_tmpdir=None`). The directory `D:\admin\code\projects` (and
  everything under it, including `dist/`) has restrictive ACLs:
  `Everyone:(CI)(DENY)(DC)` (denies directory creation) and
  `Mandatory Label\Low Mandatory Level:(OI)(CI)(NW)` (low integrity, no-write).
  The bootloader cannot create the temp dir, so extraction fails before any
  Python code runs.
- **Path bisection:** Works from `D:\admin`, `D:\admin\code`, `D:\t`,
  `C:\tmp\exetest\`. Fails from `D:\admin\code\projects` onward. The ACLs
  are inherited from the parent; `icacls` on `D:\admin\code\projects` vs
  `D:\admin\code` is the smoking gun.
- **Fix:** Set `runtime_tmpdir=""` in the PyInstaller `EXE()` spec. An empty
  string tells the bootloader to use `GetTempPathW()` (the system temp dir)
  from the start, bypassing the CWD entirely. This is a one-line change in
  `AgentOps.spec` and requires a rebuild (`python scripts/build_windows_exe.py`).
- **Remember:** PyInstaller onefile is *not* CWD-independent by default. When
  a frozen EXE fails to start with "Could not create temporary directory!",
  check `icacls` on the launch directory before blaming the bundle. The fix
  is `runtime_tmpdir=""`, not copying the EXE around. The smoke test's
  workaround (copying to workspace) was a stopgap; the real fix belongs in
  the spec.
### 2026-10-07 - PR #5 made the standard workflow autonomous (merged into main)

- **Context:** PR #5 (`feat(agentops): make standard workflow autonomous`, head `0ed030f` → merged `f742773` → commit `d0c5512`) was merged into `main` after rebasing the PR branch onto current `origin/main` and resolving stale CI failures. The standard workflow had two gaps that made it a chain of independent agents rather than one workflow: (1) dependent agents only received their own request text, so `implementation` never read the architecture plan and `review` never saw the change it was judging; (2) a failed review ended the run at "Review did not pass" instead of being repaired within the existing `max_repair_cycles` budget.
- **What changed:** `_dependency_handoff()` prepends each completed PASSED dependency's role/description/result to the dependent agent's prompt, bounded by `HANDOFF_RESULT_CHARS=2000` and `HANDOFF_TOTAL_CHARS=8000` (deterministic truncation with `[truncated]` marker, no LLM). Review now also depends on `implementation.id` so it receives the change it must judge. `_repair_failed_review()` turns a demonstrated review rejection into a bounded `debugging → verification → review` cycle, each iteration persisted as real tasks and governed by `max_repair_cycles`; READY still only from `assert_tasks_ready`; an exhausted budget returns NOT READY; a BLOCKED review (never ran) creates no repair, mirroring the UNVERIFIED rule already applied to verification. Repair context is recovered from the review task's failure rows (last attempt's message).
- **Tests added:** `tests/test_workflow_autonomy.py` with 15 tests covering handoff content/bounds, retry-history preservation, repair task creation, repair context from failures, repair→verification→re-review ordering, a passing re-review reaching READY, zero/exhausted repair budgets remaining NOT READY, and a blocked review creating no repair.
- **Evidence:** All 15 autonomy tests OK (15/15, ~15.5s); full suite OK (72/72, 1 skipped); PR `merged: true` on GitHub; `test_workflow_autonomy.py`, `HANDOFF_RESULT_CHARS`, `HANDOFF_TOTAL_CHARS`, `_dependency_handoff`, `_repair_failed_review` all present on `main`.
- **Remember:** A demonstrated review rejection is now repaired, not terminated; handoffs are bounded and deterministic; a review that never ran (BLOCKED) is not treated as a failure.