"""One dev entrypoint for the Python products in this repository.

Stdlib only, and the same command on Windows PowerShell, macOS and Linux:

    python tasks.py list                      # products and what each command runs
    python tasks.py test agentops             # one product's suite
    python tasks.py test all                  # every product's suite, in order
    python tasks.py lint                      # ruff check . (repo-wide config)
    python tasks.py lint ai-token-tracker     # ruff check on one product
    python tasks.py format-check mini-llm     # ruff format --check on one product
    python tasks.py evidence agentops         # regenerate + check its evidence

Every command runs exactly what CI and the product docs already run, from the
same directory, with the interpreter that runs this script. Nothing here
installs dependencies: install each product's requirements first, as its README
says. Ruff must be importable (`python -m pip install ruff==0.17.2`, the
version CI pins) or on PATH.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

#: name -> directory, test commands (argv after the interpreter, plus the
#: directory each runs from, relative to the repo root), evidence product name.
PRODUCTS: dict[str, dict] = {
    "agentops": {
        "directory": "agentops",
        "tests": [
            ("agentops", ["-m", "unittest", "discover", "-s", "tests"]),
            # The Issue #4 acceptance suite runs from the repo root (CI does too).
            (".", ["acceptance_test.py"]),
        ],
        "evidence": "agentops",
    },
    "ai-token-tracker": {
        "directory": "ai-token-tracker",
        "tests": [("ai-token-tracker", ["-m", "unittest", "discover", "-s", "tests"])],
        "evidence": "ai-token-tracker",
    },
    "mini-llm": {
        "directory": "small-projects/mini-llm",
        "tests": [("small-projects/mini-llm", ["-m", "unittest", "discover", "-s", "tests"])],
        "evidence": "small-projects/mini-llm",
    },
    "universal-game-agent": {
        "directory": "universal-game-agent",
        "tests": [("universal-game-agent", ["-m", "unittest", "discover", "-s", "tests"])],
        "evidence": "universal-game-agent",
    },
    "privacy-audit": {
        "directory": "small-projects/privacy_audit_tool",
        "tests": [("small-projects/privacy_audit_tool", ["-m", "unittest", "discover", "-s", "tests"])],
        "evidence": None,
    },
    "tiktok-slop-factory": {
        "directory": "tiktok-slop-factory",
        "tests": [("tiktok-slop-factory", ["-m", "pytest", "tests", "-q"])],
        "evidence": None,
    },
}

ALIASES = {
    "tracker": "ai-token-tracker",
    "uga": "universal-game-agent",
    "privacy": "privacy-audit",
    "privacy_audit_tool": "privacy-audit",
    "slop": "tiktok-slop-factory",
    "small-projects/mini-llm": "mini-llm",
}


def resolve(name: str) -> str:
    name = ALIASES.get(name, name)
    if name not in PRODUCTS:
        known = ", ".join(sorted(PRODUCTS))
        raise SystemExit(f"unknown product {name!r}; known: {known} (or 'all')")
    return name


def run(argv: list[str], cwd: Path) -> int:
    shown = " ".join(argv)
    print(f"\n>>> [{cwd.relative_to(ROOT).as_posix() or '.'}] {shown}", flush=True)
    return subprocess.call(argv, cwd=str(cwd))


def ruff_argv() -> list[str]:
    try:
        import ruff  # noqa: F401
    except ImportError:
        found = shutil.which("ruff")
        if found:
            return [found]
        raise SystemExit("ruff is not installed: python -m pip install ruff==0.17.2") from None
    return [sys.executable, "-m", "ruff"]


def cmd_test(names: list[str]) -> int:
    selected = list(PRODUCTS) if names in ([], ["all"]) else [resolve(n) for n in names]
    failed: list[str] = []
    for name in selected:
        for directory, argv in PRODUCTS[name]["tests"]:
            if run([sys.executable, *argv], ROOT / directory) != 0:
                failed.append(f"{name} ({directory})")
    if failed:
        print(f"\nFAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\nOK: {', '.join(selected)}")
    return 0


def cmd_lint(names: list[str]) -> int:
    # From the root so the repo-wide [tool.ruff] config in pyproject.toml applies.
    targets = ["."] if names in ([], ["all"]) else [PRODUCTS[resolve(n)]["directory"] for n in names]
    return run([*ruff_argv(), "check", *targets], ROOT)


def cmd_format_check(names: list[str]) -> int:
    targets = ["."] if names in ([], ["all"]) else [PRODUCTS[resolve(n)]["directory"] for n in names]
    return run([*ruff_argv(), "format", "--check", *targets], ROOT)


def cmd_evidence(names: list[str], timeout: int) -> int:
    generate = ["tools/evidence/generate.py", "--timeout", str(timeout)]
    if names not in ([], ["all"]):
        products = []
        for n in names:
            evidence = PRODUCTS[resolve(n)]["evidence"]
            if evidence is None:
                raise SystemExit(f"{resolve(n)} is not measured in the evidence file")
            products.append(evidence)
        generate += products
    code = run([sys.executable, *generate], ROOT)
    if code != 0:
        return code
    return run([sys.executable, "tools/evidence/check.py"], ROOT)


def cmd_list() -> int:
    for name, entry in PRODUCTS.items():
        print(f"{name}  ({entry['directory']}/)")
        for directory, argv in entry["tests"]:
            print(f"    test:  [{directory}] python {' '.join(argv)}")
        print(f"    evidence: {entry['evidence'] or 'not measured'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run tests, lint and evidence per product.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show products and their commands")
    for name, help_text in (
        ("test", "run product test suites (default: all)"),
        ("lint", "ruff check (default: whole repo)"),
        ("format-check", "ruff format --check (default: whole repo)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("products", nargs="*")
    ev = sub.add_parser("evidence", help="regenerate and check verification evidence")
    ev.add_argument("products", nargs="*")
    ev.add_argument("--timeout", type=int, default=1500, help="per-product timeout (s)")
    args = parser.parse_args(argv)

    if args.command == "list":
        return cmd_list()
    if args.command == "test":
        return cmd_test(args.products)
    if args.command == "lint":
        return cmd_lint(args.products)
    if args.command == "format-check":
        return cmd_format_check(args.products)
    return cmd_evidence(args.products, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
