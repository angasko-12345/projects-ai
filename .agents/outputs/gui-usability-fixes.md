# Output — GUI Usability Fixes (3 rounds, 2026-09-15)

> All driven by user reports against Release v0.1.3. Suite 289 OK (3 skips). Exe rebuilt + smoke-tested + archive-inspected (23 `agentops.*` modules) + Release asset replaced after every round. Pushed per round.

## What was built

- **No-flash (`57d591b`):** windowless-parent console flashing eliminated at all 5 spawn sites; 1s poll no longer respawns git (root cache) nor rebuilds the task tree on identical payloads (signature gate).
- **Scrollbars (`3827c7a`):** prompt/description scroll vertically; history/worktree/task trees and log/artifact lists scroll both directions; all tree columns stretch.
- **Vertical budget (`3499c90`):** rows 4/5/6 share growth 2/1/1; fixed panels trimmed; default 1060x740. Measured: notebook 210px, prompt 940x98 mapped.

## Verification

- 11 new tests total (7 spawn/poll + 3 layout + 1 row-weight); full suite 289 passing.
- Live-geometry probes (mapped Tk window, `grid_bbox`, parent-commit A/B via temp worktree) for Round 3.
- Archive inspection + startup/shutdown + process-exit verification per rebuild.

## Files

- Modified: `agentops/git.py`, `agentops/agent_run.py`, `agentops/runner.py`, `agentops/verification.py`, `agentops/verification_kernel.py`, `agentops/gui.py`, `agentops/gui_controller.py`, `tests/test_git.py`, `tests/test_agent_run.py`, `tests/test_runner.py`, `tests/test_verification_kernel.py`, `tests/test_gui.py`
- Backup: Round 1+ had no pre-backup (defect fixes on clean tree; diffs in git history)

## Follow-ups

- Opencode light review RECEIVED and closed: APPROVE, notes N1 (import order — applied), N2 (POSIX session asymmetry → A3 roadmap input), N3/N4 accepted as-is. See decisions.md sign-off.
- Residual: one-file cold-start AV-scan latency; one-dir build offered as follow-up.
- Cold-start UX (splash/progress) not addressed — candidate C1 item.
