"""Regenerate `.agents/evidence/verification.json` from real test execution.

Deterministic and clean-checkout reproducible. By default each product's suite
runs in a throwaway clone of the current commit, so the recorded result is a
statement about committed code rather than about whatever happens to be lying in
the working tree. That distinction is the whole point: this repository has had
green suites that were green only because uncommitted files were supplying the
evidence.

    python tools/evidence/generate.py            # all products, clean clone
    python tools/evidence/generate.py agentops   # one product
    python tools/evidence/generate.py --in-place # measure this working tree
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import (  # noqa: E402
    EVIDENCE_PATH,
    EXCLUSIONS,
    PRODUCTS,
    REPO_ROOT,
    SCHEMA_VERSION,
    git,
    parse_unittest_summary,
)


def _porcelain(directory: str, cwd: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--", directory], cwd=str(cwd),
        capture_output=True, text=True, check=True)
    return [line for line in completed.stdout.splitlines() if line.strip()]


def make_clean_clone(commit: str) -> Path:
    """A clone at `commit`, so the run cannot see uncommitted files."""
    target = Path(tempfile.mkdtemp(prefix="evidence-clone-"))
    clone = target / "repo"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(REPO_ROOT), str(clone)],
        check=True, capture_output=True, text=True)
    subprocess.run(["git", "checkout", "--quiet", "--detach", commit],
                   cwd=str(clone), check=True, capture_output=True, text=True)
    return clone


def run_product(entry: dict, root: Path, timeout: int) -> dict:
    directory = root / entry["directory"]
    if not directory.is_dir():
        raise SystemExit(f"{entry['product']}: {entry['directory']} does not exist")

    argv = [sys.executable, *entry["command"]]
    try:
        completed = subprocess.run(
            argv, cwd=str(directory), capture_output=True, text=True,
            check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "product": entry["product"], "directory": entry["directory"],
            "command": argv, "total": 0, "skipped": 0, "failures": 0, "errors": 0,
            "result": "fail", "duration_seconds": None,
            "note": f"the run exceeded {timeout}s and was killed; an interrupted run "
                    "is never a pass",
        }

    output = completed.stdout + completed.stderr
    record = {
        "product": entry["product"],
        "directory": entry["directory"],
        "command": argv,
        "dirty_paths": _porcelain(entry["directory"], root),
    }
    summary = parse_unittest_summary(output)
    if not summary:
        record.update({
            "total": 0, "skipped": 0, "failures": 0, "errors": 0,
            "result": "fail", "duration_seconds": None,
            "note": "the test runner printed no summary this script could parse",
        })
        record["raw_tail"] = output.strip().splitlines()[-40:]
        return record

    record.update(summary)
    if completed.returncode != 0 and summary["result"] == "pass":
        # A non-zero exit with an "OK" summary means the command failed for a
        # reason outside the suite. Recording that as a pass is exactly the
        # false green this file exists to prevent.
        record["result"] = "fail"
        record["note"] = f"exit code {completed.returncode} with an OK summary"
    return record


def build_document(records: list[dict], commit: str, root_kind: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "commit": commit,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "measured_in": root_kind,
        "clean_checkout": all(not r["dirty_paths"] for r in records),
        "command_note": (
            "Every command runs from its own product directory with the "
            "interpreter that ran this script. There is no repository-wide test "
            "command and these products share no dependencies."
        ),
        "excluded": EXCLUSIONS,
        "products": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("products", nargs="*", help="product names; default is all")
    parser.add_argument("--timeout", type=int, default=3600,
                        help="per-product timeout in seconds")
    parser.add_argument("--in-place", action="store_true",
                        help="measure this working tree instead of a clean clone")
    parser.add_argument("--output", type=Path, default=EVIDENCE_PATH)
    args = parser.parse_args(argv)

    known = {entry["product"] for entry in PRODUCTS}
    unknown = sorted(set(args.products) - known)
    if unknown:
        raise SystemExit(f"unknown product(s): {', '.join(unknown)}")
    selected = [e for e in PRODUCTS if not args.products or e["product"] in args.products]

    commit = git("rev-parse", "HEAD")
    clone: Path | None = None
    if args.in_place:
        root, root_kind = REPO_ROOT, "this working tree"
    else:
        clone = make_clean_clone(commit)
        root, root_kind = clone, f"a clean clone of {commit[:12]}"

    try:
        records = []
        for entry in selected:
            print(f"running {entry['product']} ({entry['directory']}) in {root_kind} ...",
                  flush=True)
            record = run_product(entry, root, args.timeout)
            records.append(record)
            print(f"  {record['result']}: {record['total']} tests, "
                  f"{record['skipped']} skipped, {record['failures']} failures, "
                  f"{record['errors']} errors", flush=True)
    finally:
        if clone is not None:
            shutil.rmtree(clone.parent, ignore_errors=True)

    # Keep records for products that were not re-measured, so a partial run
    # does not silently drop a product from the source of truth.
    existing: dict[str, dict] = {}
    if args.output.exists():
        try:
            existing = {r["product"]: r for r in json.loads(
                args.output.read_text(encoding="utf-8")).get("products", [])}
        except (ValueError, KeyError, TypeError):
            existing = {}
    merged = list(records) + [r for name, r in existing.items()
                             if name not in {m["product"] for m in records}]
    order = [entry["product"] for entry in PRODUCTS]
    merged.sort(key=lambda r: order.index(r["product"])
                if r["product"] in order else len(order))

    document = build_document(merged, commit, root_kind)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.output.relative_to(REPO_ROOT)}")

    failed = [r["product"] for r in merged if r["result"] != "pass"]
    if failed:
        print(f"NOT a clean measurement: {', '.join(failed)}", file=sys.stderr)
        return 1
    if not document["clean_checkout"]:
        print("NOTE: a product directory was dirty during this run; the evidence "
              "records that and `check.py` will reject it.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())