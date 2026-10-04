"""Every source file must parse on the supported interpreter floor.

The project targets Python 3.11 or newer, and CI pins 3.11, so a construct
that only became legal in 3.12 - a backslash inside an f-string expression,
for instance - is a SyntaxError that takes the whole package down at import
time. No other test here can report it: the training, model, and tokenizer
modules import torch, so on a box without it they fail on the missing
dependency long before anything notices the file does not parse.

Compiling the sources is enough to catch that class of mistake, and it needs
none of the runtime dependencies.
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCES = (
    "prepare_data.py",
    "src/__init__.py",
    "src/config.py",
    "src/dataset.py",
    "src/generate.py",
    "src/model.py",
    "src/tokenizer.py",
    "src/train.py",
)


class TestSourcesParse(unittest.TestCase):
    def test_every_source_file_compiles(self):
        for relative in SOURCES:
            path = os.path.join(ROOT, relative)
            with self.subTest(source=relative):
                with open(path, encoding="utf-8") as handle:
                    compile(handle.read(), path, "exec")


if __name__ == "__main__":
    unittest.main()