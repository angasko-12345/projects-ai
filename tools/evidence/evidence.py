"""The verification-evidence contract.

A green test run is a *measurement*, not a fact. This module defines what a
recorded measurement must contain, how to read it back, and what makes it
stale. Nothing else in the repository is allowed to restate a test count; the
files that used to are listed in ``BASELINE_FILES`` so the check can prove it.

Two entry points use this module:

* ``generate.py`` runs each product's own suite and writes the evidence file.
* ``check.py`` re-reads that file and reports anything contradictory.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCHEMA_VERSION = 1

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = REPO_ROOT / ".agents" / "evidence" / "verification.json"
EVIDENCE_REFERENCE = ".agents/evidence/verification.json"

#: Products that carry an instruction file and a documented test command.
#: Each suite runs from its own directory; there is no repository-wide command.
PRODUCTS = (
    {
        "product": "agentops",
        "directory": "agentops",
        "command": ["-m", "unittest", "discover", "-s", "tests"],
    },
    {
        "product": "universal-game-agent",
        "directory": "universal-game-agent",
        "command": ["-m", "unittest", "discover", "-s", "tests"],
    },
    {
        "product": "small-projects/mini-llm",
        "directory": "small-projects/mini-llm",
        "command": ["-m", "unittest", "discover", "-s", "tests"],
    },
    {
        "product": "ai-token-tracker",
        "directory": "ai-token-tracker",
        "command": ["-m", "unittest", "discover", "-s", "tests"],
    },
)

#: Deliberately not measured, and why. Recorded so the exclusion reads as a
#: decision rather than an oversight.
EXCLUSIONS = {
    "tiktok-slop-factory": (
        "Has a product AGENTS.md and an agreed command (pytest from "
        "tiktok-slop-factory/), but remains excluded: the suite spends ~10 "
        "minutes on real FFmpeg renders. Every test is offline and keyless "
        "(HTTP behavior runs behind faked transports; the former "
        "credential-gated test was made hermetic at the _json_request seam "
        "in 2026-10-22), so exclusion is about runtime cost, not credentials. "
        "A keyless full run is wired into .github/workflows/"
        "tiktok-slop-factory.yml."
    ),
}

#: Files that must point readers at the evidence file rather than quote a count.
BASELINE_FILES = (
    ".agents/AGENTS.md",
    "agentops/AGENTS.md",
    "universal-game-agent/AGENTS.md",
    "small-projects/mini-llm/AGENTS.md",
    "ai-token-tracker/AGENTS.md",
    "README.md",
    ".agents/memory/roadmap.md",
    ".agents/memory/opencode/bugfinding/master-bug-synthesis.md",
)

#: Of those, the instruction files may not state a count at all. A count in an
#: instruction file is a claim that outlives its evidence; this is the shape the
#: repository had before this mechanism existed.
COUNT_FREE_FILES = (
    ".agents/AGENTS.md",
    "agentops/AGENTS.md",
    "universal-game-agent/AGENTS.md",
    "small-projects/mini-llm/AGENTS.md",
    "ai-token-tracker/AGENTS.md",
    "README.md",
)

COUNT_CLAIM = re.compile(r"\b\d{1,4}\s+(?:tests?\b|run\b|skips?\b)", re.IGNORECASE)

REQUIRED_RECORD_FIELDS = (
    "product",
    "directory",
    "commit",
    "command",
    "total",
    "skipped",
    "failures",
    "errors",
    "result",
    "summary",
)
REQUIRED_DOCUMENT_FIELDS = (
    "schema_version",
    "generated_at",
    "commit",
    "python",
    "platform",
    "clean_checkout",
    "products",
)

_RAN = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s\s*$", re.MULTILINE)
_OK = re.compile(r"^OK(?: \(skipped=(\d+)\))?\s*$", re.MULTILINE)
_FAILED = re.compile(r"^FAILED \(([^)]*)\)\s*$", re.MULTILINE)
_DETAIL = re.compile(r"(failures|errors|skipped|expected failures|unexpected successes)=(\d+)")


def parse_unittest_summary(output: str) -> dict:
    """Read counts out of a unittest summary. Never guesses: no match, no record.

    The verbatim summary lines are kept in the record so a later edit to a
    number can be caught: `check_schema` re-parses them and requires agreement,
    which is what makes a hand-written "612 tests" fail instead of reading as
    a fresh measurement.

    What this cannot catch: someone who edits the count *and* the summary to
    match. A self-consistent forgery is indistinguishable from a measurement
    without re-running the suite, which is what `generate.py` is for. Staleness
    closes the useful part of that window -- a forged number cannot outlive the
    code it describes, because the next product commit invalidates the record.
    `test_a_self_consistent_forgery_is_the_documented_limit` pins this.
    """
    ran = _RAN.search(output)
    if ran is None:
        return {}
    record = {
        "total": int(ran.group(1)),
        "duration_seconds": round(float(ran.group(2)), 3),
        "skipped": 0,
        "failures": 0,
        "errors": 0,
        "summary": [ran.group(0).strip()],
    }
    failed = _FAILED.search(output)
    if failed is not None:
        for key, value in _DETAIL.findall(failed.group(1)):
            if key == "expected failures":
                continue
            record[key] = int(value)
        record["result"] = "fail"
        record["summary"].append(failed.group(0).strip())
        return record
    ok = _OK.search(output)
    if ok is None:
        return {}
    record["skipped"] = int(ok.group(1) or 0)
    record["result"] = "pass"
    record["summary"].append(ok.group(0).strip())
    return record


def git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=str(cwd or REPO_ROOT), capture_output=True,
        text=True, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd or REPO_ROOT}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout.strip()


def _git_paths(*args: str, cwd: Path | None = None) -> list[str]:
    """Run a `-z` git command and return its NUL-separated paths, unstripped."""
    completed = subprocess.run(
        ["git", *args], cwd=str(cwd or REPO_ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="surrogateescape", check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd or REPO_ROOT}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return [path for path in completed.stdout.split("\0") if path]


def _dirty_paths(directory: str, repo_root: Path) -> list[str]:
    """Paths under `directory` that differ from HEAD in the working tree."""
    entries = _git_paths("status", "--porcelain", "-z", "--untracked-files=all",
                         "--", directory, cwd=repo_root)
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        paths.append(entry[3:])
        # A rename/copy entry is followed by its source path as its own field.
        index += 2 if set(entry[:2]) & {"R", "C"} else 1
    return sorted(paths)


def check_schema(document: object) -> list[str]:
    """Shape and internal arithmetic of the evidence file itself."""
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["evidence file is not a JSON object"]

    for field in REQUIRED_DOCUMENT_FIELDS:
        if field not in document:
            problems.append(f"evidence file is missing required field {field!r}")
    if problems:
        return problems

    if document["schema_version"] != SCHEMA_VERSION:
        problems.append(
            f"schema_version is {document['schema_version']!r}, this checker "
            f"understands {SCHEMA_VERSION}"
        )
    for field in ("generated_at", "commit"):
        if not isinstance(document[field], str) or not document[field].strip():
            problems.append(f"{field} must be a non-empty string")
    if not re.fullmatch(r"[0-9a-f]{40}", str(document["commit"])):
        problems.append(f"commit {document['commit']!r} is not a full 40-character SHA")
    if not isinstance(document["clean_checkout"], bool):
        problems.append("clean_checkout must be a boolean")

    records = document["products"]
    if not isinstance(records, list) or not records:
        problems.append("products must be a non-empty list")
        return problems

    seen: set[str] = set()
    known = {entry["product"]: entry for entry in PRODUCTS}
    for index, record in enumerate(records):
        label = f"products[{index}]"
        if not isinstance(record, dict):
            problems.append(f"{label} is not an object")
            continue
        for field in REQUIRED_RECORD_FIELDS:
            if field not in record:
                problems.append(f"{label} is missing {field!r}")
        if any(field not in record for field in REQUIRED_RECORD_FIELDS):
            continue
        label = f"{record['product']}"
        if label in seen:
            problems.append(f"{label} is recorded more than once")
        seen.add(label)
        # The directory a record claims to have measured is what staleness is
        # judged against, so it must be the one this product actually owns.
        # Without this, a record can name a directory that never changes and
        # carry a count that describes code nobody looked at.
        if label in known and record["directory"] != known[label]["directory"]:
            problems.append(
                f"{label}: directory is {record['directory']!r}, but this product's "
                f"suite runs from {known[label]['directory']!r}")
        if not re.fullmatch(r"[0-9a-f]{40}", str(record["commit"])):
            problems.append(f"{label}: commit {record['commit']!r} is not a full "
                            "40-character SHA")
        for field in ("total", "skipped", "failures", "errors"):
            if not isinstance(record[field], int) or isinstance(record[field], bool) or record[field] < 0:
                problems.append(f"{label}: {field} must be a non-negative integer, "
                                f"got {record[field]!r}")
        if record["result"] not in ("pass", "fail"):
            problems.append(f"{label}: result must be 'pass' or 'fail', "
                            f"got {record['result']!r}")
        if record["total"] <= 0:
            problems.append(f"{label}: total is {record['total']}; an empty run is "
                            "never a pass")
        if record["skipped"] > record["total"]:
            problems.append(f"{label}: skipped ({record['skipped']}) exceeds total "
                            f"({record['total']})")
        if record["result"] == "pass" and (record["failures"] or record["errors"]):
            problems.append(f"{label}: recorded pass with failures="
                            f"{record['failures']} errors={record['errors']}")
        if record["result"] == "fail" and not (record["failures"] or record["errors"]):
            problems.append(f"{label}: recorded fail with no failures or errors, so "
                            "the run was not understood")
        if record["result"] == "pass" and record["skipped"] == record["total"]:
            problems.append(f"{label}: every test skipped, which is never a pass")
        # A recorded failure is a measurement too, but it can never anchor a
        # passing claim: the checker must exit non-zero while any product is
        # red so a failing suite cannot be mistaken for verified evidence.
        if record["result"] != "pass":
            problems.append(
                f"{label}: result is {record['result']!r}; only a passing "
                "result can anchor verification evidence -- fix the suite "
                "and regenerate")

        # Re-derive the counts from the recorded runner output. This is what
        # catches a hand-edited number: the fields and the summary lines the
        # runner actually printed have to agree.
        recorded = record.get("summary")
        if isinstance(recorded, list) and all(isinstance(entry, str)
                                               for entry in recorded):
            derived = parse_unittest_summary("\n".join(recorded))
            if not derived:
                problems.append(
                    f"{label}: summary {recorded!r} cannot be parsed, so the "
                    "recorded counts have no runner output behind them")
            else:
                for field in ("total", "skipped", "failures", "errors", "result"):
                    if derived[field] != record[field]:
                        problems.append(
                            f"{label}: {field} is recorded as {record[field]!r} but "
                            f"the recorded runner summary says {derived[field]!r} "
                            f"({recorded!r})")

    expected = set(known)
    recorded = seen
    for missing in sorted(expected - recorded):
        problems.append(f"{missing} has no evidence record")
    for extra in sorted(recorded - expected):
        problems.append(f"{extra} is recorded but is not a product of this repository")
    return problems


def check_staleness(document: dict, repo_root: Path = REPO_ROOT) -> list[str]:
    """Is each recorded result still a statement about the code in front of us?

    Judged per record, against the commit that record was measured at: a
    documentation commit does not invalidate a test run, a commit touching that
    product's directory does. Per-record rather than per-document because a
    partial `generate.py <product>` re-dates the whole file while the products it
    did not re-measure keep their earlier measurements -- a document-level check
    would call those current.
    """
    problems: list[str] = []
    try:
        head = git("rev-parse", "HEAD", cwd=repo_root)
    except RuntimeError as error:
        return [f"cannot resolve HEAD: {error}"]

    for record in document["products"]:
        label = record["product"]
        commit = record["commit"]
        # The record describes committed code. Uncommitted edits (or untracked,
        # non-ignored files) under the product dir mean the tree in front of us
        # is not what was measured. A CI checkout is clean, so this only ever
        # fires locally; generate.py does not call it (it measures in a clean
        # clone and records its own `dirty_paths`).
        try:
            dirty = _dirty_paths(record["directory"], repo_root)
        except RuntimeError as error:
            problems.append(f"{label}: cannot read working-tree status: {error}")
            dirty = []
        if dirty:
            shown = ", ".join(dirty[:5])
            more = f" (+{len(dirty) - 5} more)" if len(dirty) > 5 else ""
            problems.append(
                f"{label}: evidence is stale: {len(dirty)} uncommitted change(s) "
                f"under {record['directory']}/ ({shown}{more}). Commit or discard "
                f"them, then regenerate with `python tools/evidence/generate.py "
                f"{label}`.")
        try:
            git("cat-file", "-e", f"{commit}^{{commit}}", cwd=repo_root)
        except RuntimeError as error:
            problems.append(f"{label}: cannot resolve the recorded commit "
                            f"{commit}: {error}")
            continue
        if head == commit:
            continue
        try:
            git("merge-base", "--is-ancestor", commit, head, cwd=repo_root)
        except RuntimeError:
            problems.append(
                f"{label}: evidence was measured at {commit[:12]}, which is not an "
                f"ancestor of HEAD {head[:12]}; the history it described is gone")
            continue
        directory = record["directory"]
        # -z: paths come back raw and NUL-separated; without it git C-quotes a
        # non-ASCII path (core.quotePath), the leading '"' defeats the prefix
        # match below, and a change to agentops/café.py goes unnoticed.
        # --no-renames: a rename out of the directory is listed as a deletion
        # of the old path, not only as an addition of the new one elsewhere.
        changed = _git_paths("diff", "--name-only", "--no-renames", "-z",
                             f"{commit}..{head}", cwd=repo_root)
        touched = sorted(
            path for path in changed
            if path == directory or path.startswith(directory + "/")
        )
        if touched:
            shown = ", ".join(touched[:5])
            more = f" (+{len(touched) - 5} more)" if len(touched) > 5 else ""
            problems.append(
                f"{label}: evidence is stale: {len(touched)} file(s) under "
                f"{directory}/ changed after {commit[:12]} ({shown}{more}). "
                f"Regenerate with `python tools/evidence/generate.py {label}`."
            )

    if document["clean_checkout"] is not True:
        problems.append(
            "evidence was recorded with a dirty working tree in at least one "
            "product directory, so it is not reproducible; regenerate from a clean "
            "checkout"
        )
    return problems


def check_documents(document: dict, repo_root: Path = REPO_ROOT) -> list[str]:
    """Do the written files still tell a reader the same thing?"""
    problems: list[str] = []
    for relative in BASELINE_FILES:
        path = repo_root / relative
        if not path.exists():
            problems.append(f"{relative} is missing, so it cannot point at "
                            f"{EVIDENCE_REFERENCE}")
            continue
        if EVIDENCE_REFERENCE not in path.read_text(encoding="utf-8"):
            problems.append(
                f"{relative} does not reference {EVIDENCE_REFERENCE}; a reader "
                "there has no route to the current verification result"
            )
    for relative in COUNT_FREE_FILES:
        path = repo_root / relative
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = COUNT_CLAIM.search(line)
            if match:
                problems.append(
                    f"{relative}:{number} states a measured count "
                    f"({match.group(0)!r}) in an instruction file; state the "
                    f"command and point at {EVIDENCE_REFERENCE} instead"
                )
    return problems


def check(document: object, repo_root: Path = REPO_ROOT) -> list[str]:
    problems = check_schema(document)
    if problems:
        return problems
    return check_staleness(document, repo_root) + check_documents(document, repo_root)
