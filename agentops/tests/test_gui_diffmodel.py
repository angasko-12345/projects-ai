"""Qt-free unified-diff parsing behind the diff pane."""

from __future__ import annotations

import unittest

from agentops.gui.diffmodel import parse_unified_diff

SAMPLE = """\
diff --git a/auth/routes.py b/auth/routes.py
index 1..2 100644
--- a/auth/routes.py
+++ b/auth/routes.py
@@ -1,3 +1,4 @@
-from flask import Blueprint
+from flask import Blueprint, request
+from auth import tokens
\x20
 bp = 1
@@ -8,2 +9,3 @@ def login():
 def login():
-\treturn 1
+\treturn 2
+--- not a header
\\ No newline at end of file
diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
"""


class ParseUnifiedDiffTests(unittest.TestCase):
    def setUp(self):
        self.files = parse_unified_diff(SAMPLE)

    def test_files_paths_and_counts(self):
        summary = [(f.path, f.removed, f.added) for f in self.files]
        self.assertEqual(summary, [("auth/routes.py", 2, 4), ("old.txt", 2, 0), ("logo.png", 0, 0)])

    def test_one_gutter_numbers_and_gap_rows(self):
        rows = [(r.kind, r.number) for r in self.files[0].rows]
        self.assertEqual(
            rows,
            [("del", 1), ("add", 1), ("add", 2), ("ctx", 3), ("ctx", 4),
             ("gap", 4), ("ctx", 9), ("del", 9), ("add", 10), ("add", 11)],
        )

    def test_body_text_tabs_and_lookalike_lines(self):
        texts = [r.text for r in self.files[0].rows if r.kind == "add"]
        self.assertEqual(texts[-2:], ["    return 2", "--- not a header"])

    def test_deleted_and_binary_files(self):
        self.assertEqual([r.number for r in self.files[1].rows], [1, 2])
        self.assertEqual([(r.kind, r.text) for r in self.files[2].rows], [("note", "Binary file changed")])

    def test_non_diff_text_has_no_files(self):
        self.assertEqual(parse_unified_diff("1 file changed, 2 insertions(+)\n"), [])
        self.assertEqual(parse_unified_diff(""), [])


if __name__ == "__main__":
    unittest.main()
