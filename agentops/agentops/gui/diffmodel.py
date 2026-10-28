"""Qt-free unified-diff parsing for the diff pane (``gui/diffview.py``).

``parse_unified_diff`` turns ``git diff`` text into per-file rows for one
line-number gutter: added and context rows carry the new-side number, deleted
rows the old-side number, and the lines skipped between hunks become one
``gap`` row ("n unmodified lines").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_KINDS = {"+": "add", "-": "del", " ": "ctx"}


@dataclass
class DiffRow:
    kind: str  # "add" | "del" | "ctx" | "gap" (count in number) | "note"
    text: str = ""
    number: int | None = None


@dataclass
class FileDiff:
    path: str
    added: int = 0
    removed: int = 0
    rows: list[DiffRow] = field(default_factory=list)


def _header_path(line: str) -> str:
    return line.rpartition(" b/")[2].strip('"') or line


def parse_unified_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    in_hunk = False
    old = new = shown = 0  # next old/new line numbers; first new line not yet shown
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = FileDiff(_header_path(line))
            files.append(current)
            in_hunk, shown = False, 1
            continue
        if current is None:
            continue
        match = _HUNK.match(line)
        if match:
            old, new = int(match.group(1)), int(match.group(3))
            start = new if match.group(4) != "0" else new + 1
            if start > shown:
                current.rows.append(DiffRow("gap", number=start - shown))
            in_hunk, shown = True, start
            continue
        if not in_hunk:
            if line.startswith("+++ ") and line[4:] != "/dev/null":
                current.path = line[4:].removeprefix("b/").strip('"')
            elif line.startswith("Binary files "):
                current.rows.append(DiffRow("note", "Binary file changed"))
            continue
        kind = _KINDS.get(line[:1], "ctx" if not line else "")
        if not kind:  # "\ No newline at end of file"
            continue
        body = line[1:].replace("\t", "    ")
        if kind == "del":
            current.rows.append(DiffRow(kind, body, old))
            current.removed += 1
            old += 1
            continue
        current.rows.append(DiffRow(kind, body, new))
        if kind == "add":
            current.added += 1
        else:
            old += 1
        new += 1
        shown = new
    return files
