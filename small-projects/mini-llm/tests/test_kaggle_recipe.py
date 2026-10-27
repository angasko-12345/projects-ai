"""Kaggle T4 recipe: notebook shape, TinyStories conversion, and a CPU end-to-end run.

The real recipe needs a GPU and ~200M tokens; here the same code path runs on
CPU with a tiny corpus and model so it is known to execute: download skipped
via --input, prepare, 10-step smoke + resume, two seeds, full-val evaluate,
GGUF export (or its skip message), results.md. What it cannot show is T4 speed
or fp16 stability; those are measured by the smoke cell on Kaggle.
"""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PKG, "kaggle"))

import build_notebook  # noqa: E402
import run_recipe  # noqa: E402

NOTEBOOK = os.path.join(PKG, "kaggle", "train_tinystories.ipynb")

STORY_LINES = [
    "Once upon a time, there was a little cat named Tom. Tom liked to play in the sun.",
    "One day, Tom saw a big red ball. He ran to the ball and pushed it with his nose.",
    "The ball rolled away and Tom laughed. He was very happy and played all day long.",
    "Lily had a small dog. The dog was very kind and liked to run in the park with her.",
]


def tinystories_text(n_stories: int, marker: str = "<|endoftext|>") -> str:
    """TinyStories-format text: stories separated by marker lines, with blank lines inside."""
    out = []
    for i in range(n_stories):
        a, b = STORY_LINES[i % 4], STORY_LINES[(i + 1) % 4]
        out.append(f"{a}\n\n{b} Story {i}.\n{marker}\n")
    return "".join(out)


class TestNotebook(unittest.TestCase):
    def setUp(self):
        with open(NOTEBOOK, encoding="utf-8") as f:
            self.raw = f.read()
        self.nb = json.loads(self.raw)

    def code(self):
        return ["".join(c["source"]) for c in self.nb["cells"] if c["cell_type"] == "code"]

    def test_committed_notebook_is_what_the_builder_writes(self):
        self.assertEqual(
            self.raw,
            build_notebook.render(),
            "run python kaggle/build_notebook.py and commit the result",
        )

    def test_is_nbformat_4_with_clean_code_cells(self):
        self.assertEqual(self.nb["nbformat"], 4)
        self.assertEqual(self.nb["metadata"]["kernelspec"]["name"], "python3")
        for cell in self.nb["cells"]:
            self.assertIn(cell["cell_type"], ("markdown", "code"))
            self.assertIsInstance(cell["source"], list)
            if cell["cell_type"] == "code":
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])

    def test_validates_against_the_nbformat_schema(self):
        try:
            import nbformat
        except ImportError:
            self.skipTest("nbformat not installed (schema check runs where it is)")
        nbformat.validate(nbformat.reads(self.raw, as_version=4))

    def test_every_code_cell_compiles(self):
        for i, src in enumerate(self.code()):
            with self.subTest(cell=i):
                compile(src, f"<cell {i}>", "exec")

    def test_smoke_runs_before_full_and_export_last(self):
        src = "\n".join(self.code())
        order = [
            src.index(f'recipe("{stage}")') for stage in ("smoke", "full", "export", "summary")
        ]
        self.assertEqual(order, sorted(order))

    def test_settings_name_the_ref_and_the_gpu_check_refuses_cpu(self):
        settings, gpu = self.code()[0], self.code()[1]
        self.assertIn('REPO_REF = "main"', settings)
        self.assertIn("GPU T4 x2", gpu)


class TestConvertTinyStories(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def convert(self, text, max_bytes=None):
        src = os.path.join(self.tmp.name, "in.txt")
        dst = os.path.join(self.tmp.name, "out.txt")
        with open(src, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        n = run_recipe.convert_tinystories(src, dst, max_bytes)
        with open(dst, encoding="utf-8") as f:
            return n, f.read()

    def test_one_passage_per_story_and_inner_blank_lines_removed(self):
        n, out = self.convert("A one.\n\nA two.\n<|endoftext|>\nB one.\n<|endoftext|>\n")
        self.assertEqual(n, 2)
        self.assertEqual(out, "A one.\nA two.\n\nB one.\n\n")
        self.assertEqual([p for p in out.split("\n\n") if p.strip()], ["A one.\nA two.", "B one."])

    def test_zero_width_space_marker_crlf_and_unterminated_tail(self):
        n, out = self.convert("A.\r\n<\u200b|endoftext|>\r\n  \r\nB.\r\nC.")
        self.assertEqual(n, 2)
        self.assertEqual(out, "A.\n\nB.\nC.\n\n")
        self.assertNotIn("endoftext", out)

    def test_max_bytes_stops_at_a_story_boundary(self):
        text = tinystories_text(20)
        n, out = self.convert(text, max_bytes=len(text.encode()) // 4)
        self.assertGreater(n, 0)
        self.assertLess(n, 20)
        self.assertTrue(out.endswith(".\n\n"))


class TestRendering(unittest.TestCase):
    def test_nothing_measured_means_blank_fields_not_numbers(self):
        text = run_recipe.render_results({}, run_recipe.RECIPE, {})
        self.assertNotIn("NOT the baseline", text)
        for seed in run_recipe.RECIPE["seeds"]:
            self.assertIn(f"| {seed} |  |  |  |  |  |  |", text)
        self.assertIn("val_bpb mean: \n", text)

    def test_recipe_token_budget_matches_the_docs(self):
        r = run_recipe.RECIPE
        tokens = r["max_steps"] * r["batch_size"] * r["grad_accum_steps"] * r["context_length"]
        self.assertEqual(tokens, 196_608_000)
        with open(os.path.join(PKG, "docs", "RESULTS.md"), encoding="utf-8") as f:
            doc = f.read()
        self.assertIn(r["name"], doc)
        self.assertIn(f"{tokens:,}", doc)
        self.assertEqual(
            (r["vocab_size"], r["d_model"], r["n_layers"], r["amp"]), (8192, 384, 6, "fp16")
        )

    def test_bad_override_and_unknown_seed_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = ["--stage", "summary", "--out-dir", tmp, "--data-dir", tmp]
            with self.assertRaises(SystemExit):
                run_recipe.main(base + ["--override", "not_a_key=1"])
            with self.assertRaises(SystemExit):
                run_recipe.main(base + ["--only-seed", "5"])


class TestCpuEndToEnd(unittest.TestCase):
    """The whole recipe, tiny, on CPU: every stage runs and every number is measured."""

    TINY = [
        "vocab_size=300",
        "context_length=16",
        "n_layers=1",
        "n_heads=2",
        "d_model=16",
        "d_ff=32",
        "batch_size=2",
        "max_steps=6",
        "warmup_steps=2",
        "eval_interval=3",
        "eval_batches=2",
        "smoke_steps=4",
        "smoke_resume_steps=2",
        "seeds=11,12",
        "amp=bf16",
        "eval_batch_size=4",
        "min_frequency=1",
        "val_frac=0.1",
    ]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.corpus = os.path.join(cls.tmp.name, "TinyStories-train.txt")
        with open(cls.corpus, "w", encoding="utf-8") as f:
            f.write(tinystories_text(60))
        cls.out = os.path.join(cls.tmp.name, "out")
        cls.args = [
            "--device",
            "cpu",
            "--input",
            cls.corpus,
            "--out-dir",
            cls.out,
            "--data-dir",
            os.path.join(cls.tmp.name, "data"),
            "--no-quantize",
        ]
        for item in cls.TINY:
            cls.args += ["--override", item]
        with contextlib.redirect_stdout(io.StringIO()) as log:
            run_recipe.main(["--stage", "all", *cls.args])
        cls.log = log.getvalue()
        with open(os.path.join(cls.out, "run.json"), encoding="utf-8") as f:
            cls.state = json.load(f)
        with open(os.path.join(cls.out, "results.md"), encoding="utf-8") as f:
            cls.results = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_smoke_checks_pass_and_measure_throughput(self):
        smoke = self.state["smoke"]
        self.assertTrue(smoke["checks"]["val_loss_finite"])
        self.assertTrue(smoke["checks"]["resume_ran"])
        self.assertIsNone(smoke["checks"]["scaler_restored"])  # bf16: no scaler
        self.assertGreater(smoke["train"]["tokens_per_sec"], 0)
        self.assertEqual(smoke["projection"]["full_run_tokens"], 6 * 2 * 16)

    def test_both_seeds_trained_and_evaluated_with_finite_bpb(self):
        for seed in ("11", "12"):
            entry = self.state["seeds"][seed]
            self.assertEqual(entry["train"]["steps_run"], 6)
            self.assertTrue(math.isfinite(entry["eval"]["val_bpb"]))
            self.assertTrue(os.path.exists(entry["checkpoint"]))
            self.assertEqual(len(entry["eval"]["samples"]), 5)
        self.assertNotEqual(
            self.state["seeds"]["11"]["eval"]["val_loss"],
            self.state["seeds"]["12"]["eval"]["val_loss"],
        )

    def test_provenance_and_data_identity_are_recorded(self):
        data = self.state["data"]
        self.assertEqual(data["stories"], 60)
        self.assertEqual(len(data["raw_sha256"]), 64)
        prov = self.state["seeds"]["11"]["provenance"]
        for key in ("utc", "torch", "python", "git_commit"):
            self.assertIn(key, prov)
        for name in ("tokenizer.json", "meta.json", "prepared.json"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)

    def test_export_ran_or_said_why_not(self):
        export = self.state["export"]
        if os.path.exists(os.path.join(PKG, "src", "export_gguf.py")):
            self.assertGreater(export.get("f16_bytes", 0), 0)
        else:
            self.assertIn("export_gguf.py", export["skipped"])

    def test_results_are_marked_not_baseline_and_carry_measured_values(self):
        self.assertTrue(self.results.startswith("> **NOT the baseline recipe**"))
        bpb = self.state["seeds"]["11"]["eval"]["val_bpb"]
        self.assertIn(f"{bpb:.4f}", self.results)

    def test_rerunning_full_skips_finished_seeds(self):
        with contextlib.redirect_stdout(io.StringIO()) as log:
            run_recipe.main(["--stage", "full", *self.args])
        self.assertIn("prepared data already in", log.getvalue())
        self.assertEqual(log.getvalue().count("already done, skipping"), 2)


if __name__ == "__main__":
    unittest.main()
