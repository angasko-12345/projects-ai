"""Evidence contracts: what each role must produce before it can PASS.

A role whose expected evidence is WORKTREE_CHANGE cannot be satisfied by an agent
that exits 0 and changes nothing. That combination was observed live on
2026-10-05: a constrained opencode task returned exit_code=0, `implementation`
was recorded PASSED, and the worktree was empty -- the delegate did nothing.

`RunResult.succeeded` is `not timed_out and exit_code == 0`, which is evidence
that the process ran, not evidence that the work happened. Treating the former as
the latter is the same smell as a SKIPPED check counting as a passed one.

Kept in its own module so the rule is stated once and both the workflow and the
tests read it, rather than being inlined at the assignment site.
"""

from __future__ import annotations

import re
from enum import StrEnum


class TaskEvidence(StrEnum):
    """What a role must produce for PASSED to mean anything."""

    #: Any success signal at all (process exited cleanly, no artifact required).
    PROCESS = "process"
    #: A change in the working tree. For roles that exist to change code.
    WORKTREE_CHANGE = "worktree_change"
    #: A non-empty textual result. For research/analysis, where the answer IS
    #: the deliverable and "investigate why X fails" legitimately writes nothing.
    RESULT_TEXT = "result_text"


#: Roles whose purpose is to change code. These may not PASS on a bare exit 0.
ROLES_REQUIRING_WORKTREE_CHANGE = frozenset({"implementation"})

#: Roles whose deliverable is an answer. No file change expected or required.
ROLES_REQUIRING_RESULT_TEXT = frozenset({"research", "architecture", "review"})

#: Roles that produce no artifact of their own.
ROLES_REQUIRING_PROCESS_ONLY = frozenset({"verification", "debugging"})


def expected_evidence(role: str) -> TaskEvidence:
    """The evidence ``role`` must produce. Unknown roles get PROCESS, which
    preserves today's behaviour instead of guessing at a requirement."""
    if role in ROLES_REQUIRING_WORKTREE_CHANGE:
        return TaskEvidence.WORKTREE_CHANGE
    if role in ROLES_REQUIRING_RESULT_TEXT:
        return TaskEvidence.RESULT_TEXT
    if role in ROLES_REQUIRING_PROCESS_ONLY:
        return TaskEvidence.PROCESS
    return TaskEvidence.PROCESS


def evidence_present(
    role: str,
    *,
    files_changed: tuple[str, ...] = (),
    result_text: str | None = None,
    observed: bool = True,
) -> tuple[bool, str]:
    """``(satisfied, reason_if_not)`` for ``role``.

    ``observed`` says whether the evidence channel was actually readable. It is
    False when no AgentRunOutcome row exists AND the transcript yielded nothing
    to parse -- i.e. we could not determine what changed, as opposed to
    determining that nothing changed. Those are different states and conflating
    them would fail every custom or stubbed runner that never persists a run
    row. An unobservable channel is reported as satisfied and left to
    verification, which is the layer whose job that is; an observed empty
    worktree is a genuine failure.
    """
    requirement = expected_evidence(role)
    if requirement is TaskEvidence.WORKTREE_CHANGE and observed and not files_changed:
        return False, (
            "no_evidence: the agent exited cleanly but changed no files, so it "
            "did not do the work it was asked to do"
        )
    return True, ""


_GIT_STATUS_LINE = re.compile(r"^\s*([MADRCU?!]{1,2})\s+(.+?)\s*$")


def files_changed_from_transcript(transcript: str | None) -> tuple[str, ...]:
    """Best-effort recovery of changed paths from an agent's own transcript.

    Used only when no AgentRunOutcome row exists -- a custom or stubbed runner
    that never persisted one. The runner's metadata collector uses
    `git status --porcelain --untracked-files=all`, so that shape is what this
    parses. Anything unparseable yields (), which for a WORKTREE_CHANGE role
    means "not satisfied": absence of evidence is treated as absence of work,
    never as a pass.
    """
    if not transcript:
        return ()
    found: list[str] = []
    for line in transcript.splitlines():
        match = _GIT_STATUS_LINE.match(line)
        if match:
            path = match.group(2).strip().strip('"')
            if path and path not in found:
                found.append(path)
    return tuple(found)
