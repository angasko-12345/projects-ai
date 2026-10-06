"""Check that the recorded verification evidence is still true.

Run from anywhere:

    python tools/evidence/check.py

Exit code 0 means the evidence file is a valid, current, reproducible
measurement and that the instruction files point at it instead of restating
their own counts. Exit code 1 prints every contradiction it found.

This checker is itself tested, including a corruption test: see
`tools/evidence/test_check.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import EVIDENCE_PATH, REPO_ROOT, check  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_PATH)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)

    if not args.evidence.exists():
        print(f"FAIL: {args.evidence} does not exist. Run "
              f"`python tools/evidence/generate.py`.", file=sys.stderr)
        return 1
    try:
        document = json.loads(args.evidence.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"FAIL: {args.evidence} is not valid JSON: {error}", file=sys.stderr)
        return 1

    problems = check(document, args.repo_root)
    if problems:
        print(f"FAIL: {len(problems)} problem(s) with {args.evidence}:",
              file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    commit = document["commit"]
    print(f"OK: evidence recorded at {commit[:12]} on {document['generated_at']} "
          f"is a clean, reproducible measurement")
    for record in document["products"]:
        print(f"  {record['product']}: {record['result']}, {record['total']} tests, "
              f"{record['skipped']} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())