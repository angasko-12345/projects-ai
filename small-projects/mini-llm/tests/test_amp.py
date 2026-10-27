"""Mixed precision (--amp off|fp16|bf16).

Pinned here: off is the default and trains bit-identically to the plain fp32
loop; bf16 runs on CPU with finite losses (also with grad accumulation and the
chunked loss); fp16 without CUDA fails before any work; GradScaler state is
saved under weights_only-safe types and restored on resume; clipping sees
unscaled gradients; a resume cannot silently switch precision; and a
checkpoint written before AMP existed still resumes. Real fp16 training is
CUDA-only and runs only where a GPU is present.
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config, load_data_meta
from src.dataset import build_dataloader
from src.model import from_config
from src.train import (
    _atomic_torch_save,
    apply_cli_config,
    build_param_groups,
    lr_at_step,
    make_grad_scaler,
    make_parser,
    optimizer_step,
    read_checkpoint,
    resolve_amp,
    restore_grad_scaler,
    save_checkpoint,
    set_seed,
    train,
)
from tests.test_grad_accum import build_data

CPU = torch.device("cpu")


class TestAmpConfigAndCli(unittest.TestCase):
    def test_default_is_off_and_stored(self):
        self.assertEqual(Config().amp, "off")
        self.assertEqual(Config().to_dict()["amp"], "off")

    def test_unknown_mode_is_rejected(self):
        for bad in ("fp32", "FP16", "", None):
            with self.subTest(amp=bad), self.assertRaises(AssertionError):
                Config(amp=bad)

    def test_old_config_dict_without_amp_reads_as_off(self):
        d = Config().to_dict()
        del d["amp"]
        self.assertEqual(Config.from_dict(d).amp, "off")

    def test_flag_defaults_to_none_and_bare_invocation_changes_nothing(self):
        args = make_parser().parse_args([])
        self.assertIsNone(args.amp)
        cfg = Config()
        before = cfg.to_dict()
        apply_cli_config(cfg, args)
        self.assertEqual(cfg.to_dict(), before)

    def test_flag_sets_the_field(self):
        cfg = Config()
        apply_cli_config(cfg, make_parser().parse_args(["--amp", "bf16"]))
        self.assertEqual(cfg.amp, "bf16")

    def test_flag_choices_are_enforced(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            make_parser().parse_args(["--amp", "fp8"])


class TestResolveAmp(unittest.TestCase):
    def test_off_is_plain_fp32(self):
        self.assertIsNone(resolve_amp("off", CPU))
        self.assertIsNone(make_grad_scaler(CPU, None))

    def test_bf16_on_cpu_has_no_scaler(self):
        self.assertIs(resolve_amp("bf16", CPU), torch.bfloat16)
        self.assertIsNone(make_grad_scaler(CPU, torch.bfloat16))

    def test_fp16_on_cpu_fails_fast_with_a_reason(self):
        with self.assertRaises(SystemExit) as ctx:
            resolve_amp("fp16", CPU)
        self.assertIn("CUDA", str(ctx.exception))
        self.assertIn("bf16", str(ctx.exception))


class TestOptimizerStep(unittest.TestCase):
    """With a GradScaler, clipping must see the unscaled gradient norm."""

    def setUp(self):
        torch.manual_seed(0)
        self.model = torch.nn.Linear(4, 3)
        self.x = torch.randn(8, 4)

    def grads_norm(self):
        return float(torch.sqrt(sum((p.grad**2).sum() for p in self.model.parameters())))

    def test_clip_sees_unscaled_gradients(self):
        self.model(self.x).pow(2).mean().backward()
        true_norm = self.grads_norm()
        self.model.zero_grad()

        opt = torch.optim.SGD(self.model.parameters(), lr=0.1)
        scaler = torch.amp.GradScaler("cpu", init_scale=1024.0)
        scaler.scale(self.model(self.x).pow(2).mean()).backward()
        self.assertGreater(self.grads_norm(), 100 * true_norm)  # really scaled

        seen = []
        real = torch.nn.utils.clip_grad_norm_

        def spy(params, max_norm, *a, **k):
            params = list(params)
            seen.append(float(torch.sqrt(sum((p.grad**2).sum() for p in params))))
            return real(params, max_norm, *a, **k)

        with mock.patch("torch.nn.utils.clip_grad_norm_", spy):
            optimizer_step(self.model, opt, scaler, grad_clip=1e9)
        self.assertEqual(len(seen), 1)
        self.assertAlmostEqual(seen[0], true_norm, places=4)

    def test_without_scaler_is_clip_then_step(self):
        ref = torch.nn.Linear(4, 3)
        ref.load_state_dict(self.model.state_dict())
        for m in (self.model, ref):
            m(self.x).pow(2).mean().backward()
        opt_a = torch.optim.SGD(self.model.parameters(), lr=0.1)
        opt_b = torch.optim.SGD(ref.parameters(), lr=0.1)
        optimizer_step(self.model, opt_a, None, grad_clip=0.01)
        torch.nn.utils.clip_grad_norm_(ref.parameters(), 0.01)
        opt_b.step()
        for a, b in zip(self.model.parameters(), ref.parameters(), strict=True):
            self.assertTrue(torch.equal(a, b))


class TestScalerCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)

    def cfg(self):
        return Config(
            vocab_size=load_data_meta(self.train_bin)["vocab_size"],
            context_length=16,
            n_layers=1,
            n_heads=2,
            d_model=16,
            d_ff=32,
            train_bin=self.train_bin,
            val_bin=os.path.join(self.tmp.name, "val.bin"),
            tokenizer_path=os.path.join(self.tmp.name, "tokenizer.json"),
            max_steps=4,
            warmup_steps=1,
        )

    def test_scaler_state_round_trips_under_weights_only(self):
        cfg = self.cfg()
        model = from_config(cfg)
        opt = torch.optim.AdamW(model.parameters())
        scaler = torch.amp.GradScaler("cpu", init_scale=512.0, growth_interval=7)
        # Move the growth tracker off its initial value so the test sees it travel.
        scaler.scale(model.wte.weight.sum()).backward()
        scaler.step(opt)
        scaler.update()
        path = os.path.join(self.tmp.name, "s.pt")
        with contextlib.redirect_stderr(io.StringIO()):
            save_checkpoint(path, model, opt, 1, cfg, scaler=scaler)
        ckpt = read_checkpoint(path)  # weights_only=True inside
        self.assertEqual(ckpt["scaler_state"], scaler.state_dict())
        fresh = torch.amp.GradScaler("cpu")
        with contextlib.redirect_stdout(io.StringIO()):
            restore_grad_scaler(fresh, ckpt, path)
        self.assertEqual(fresh.state_dict(), scaler.state_dict())

    def test_no_scaler_writes_no_scaler_key(self):
        cfg = self.cfg()
        model = from_config(cfg)
        path = os.path.join(self.tmp.name, "n.pt")
        with contextlib.redirect_stderr(io.StringIO()):
            save_checkpoint(path, model, torch.optim.AdamW(model.parameters()), 1, cfg)
        self.assertNotIn("scaler_state", read_checkpoint(path))

    def test_missing_scaler_state_is_reported_not_fatal(self):
        scaler = torch.amp.GradScaler("cpu", init_scale=8.0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            restore_grad_scaler(scaler, {}, "old.pt")
        self.assertIn("no GradScaler state", err.getvalue())
        self.assertEqual(scaler.get_scale(), 8.0)


class TrainingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.train_bin = build_data(self.tmp.name, context_length=16)
        self.ckpt_dir = os.path.join(self.tmp.name, "ckpts")

    def tiny(self, **over) -> Config:
        kw = dict(
            vocab_size=load_data_meta(self.train_bin)["vocab_size"],
            context_length=16,
            n_layers=2,
            n_heads=2,
            d_model=32,
            d_ff=64,
            batch_size=2,
            max_steps=5,
            warmup_steps=2,
            eval_interval=5,
            eval_batches=1,
            train_bin=self.train_bin,
            val_bin=os.path.join(self.tmp.name, "val.bin"),
            tokenizer_path=os.path.join(self.tmp.name, "tokenizer.json"),
            checkpoint_dir=self.ckpt_dir,
            seed=3,
        )
        kw.update(over)
        return Config(**kw)

    def run_train(self, cfg, **kw):
        with (
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            train(cfg, device_spec="cpu", **kw)
        return out.getvalue()

    def final(self):
        return read_checkpoint(os.path.join(self.ckpt_dir, "final.pt"))


class TestAmpOffIsTheFp32Loop(TrainingCase):
    def reference_weights(self, cfg: Config) -> dict:
        """The pre-AMP training loop written out by hand: seed, init, AdamW,
        warmup/cosine, accumulate, clip, step. No autocast, no scaler."""
        set_seed(cfg.seed)
        model = from_config(cfg)
        loader = build_dataloader(cfg.train_bin, cfg.context_length, cfg.batch_size, shuffle=True)
        opt = torch.optim.AdamW(
            build_param_groups(model, cfg.weight_decay),
            lr=cfg.learning_rate,
            betas=(cfg.beta1, cfg.beta2),
            weight_decay=cfg.weight_decay,
        )
        it = iter(loader)
        model.train()
        for step in range(1, cfg.max_steps + 1):
            for group in opt.param_groups:
                group["lr"] = lr_at_step(step, cfg)
            opt.zero_grad()
            for _ in range(cfg.grad_accum_steps):
                try:
                    x, y = next(it)
                except StopIteration:
                    it = iter(loader)
                    x, y = next(it)
                _, loss = model(x, y)
                (loss / cfg.grad_accum_steps).backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            opt.step()
        return model.state_dict()

    def assert_same_weights(self, cfg):
        self.run_train(cfg)
        got = self.final()["model_state"]
        want = self.reference_weights(cfg)
        self.assertEqual(set(got), set(want))
        for name in want:
            self.assertTrue(torch.equal(got[name], want[name]), f"{name} differs")

    def test_off_matches_the_plain_loop_bit_for_bit(self):
        self.assert_same_weights(self.tiny(amp="off"))

    def test_off_with_accumulation_matches_bit_for_bit(self):
        self.assert_same_weights(self.tiny(amp="off", grad_accum_steps=2))

    def test_off_checkpoint_has_no_scaler_and_records_off(self):
        self.run_train(self.tiny())
        ckpt = self.final()
        self.assertNotIn("scaler_state", ckpt)
        self.assertEqual(ckpt["config"]["amp"], "off")


class TestBf16OnCpu(TrainingCase):
    def assert_finite_run(self, cfg):
        log = self.run_train(cfg)
        self.assertIn("amp: bf16 autocast", log)
        ckpt = self.final()
        for key in ("train_loss", "val_loss", "val_bpb"):
            self.assertTrue(torch.isfinite(torch.tensor(ckpt[key])), key)
        self.assertEqual(ckpt["config"]["amp"], "bf16")
        self.assertNotIn("scaler_state", ckpt)
        for name, t in ckpt["model_state"].items():
            self.assertEqual(t.dtype, torch.float32, f"{name} stored as {t.dtype}")

    def test_five_steps_are_finite_and_weights_stay_fp32(self):
        self.assert_finite_run(self.tiny(amp="bf16"))

    def test_with_accumulation_and_chunked_loss(self):
        self.assert_finite_run(self.tiny(amp="bf16", grad_accum_steps=2, loss_chunk_size=4))

    def test_resume_continues_in_bf16(self):
        self.run_train(self.tiny(amp="bf16", max_steps=3, eval_interval=3))
        self.run_train(
            self.tiny(amp="bf16", max_steps=5, eval_interval=5),
            resume_from=os.path.join(self.ckpt_dir, "final.pt"),
        )
        self.assertEqual(self.final()["step"], 5)


class TestAmpRefusals(TrainingCase):
    def test_fp16_on_cpu_fails_before_writing_anything(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_train(self.tiny(amp="fp16"))
        self.assertIn("CUDA", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.ckpt_dir, "final.pt")))

    def test_resume_with_a_different_amp_is_refused(self):
        self.run_train(self.tiny(max_steps=3, eval_interval=3))
        with self.assertRaises(SystemExit) as ctx:
            self.run_train(
                self.tiny(amp="bf16"), resume_from=os.path.join(self.ckpt_dir, "final.pt")
            )
        self.assertIn("--amp off", str(ctx.exception))

    def test_checkpoint_from_before_amp_still_resumes(self):
        self.run_train(self.tiny(max_steps=3, eval_interval=3))
        path = os.path.join(self.ckpt_dir, "final.pt")
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        del ckpt["config"]["amp"]  # what every pre-AMP checkpoint looks like
        _atomic_torch_save(ckpt, path)
        self.run_train(self.tiny(max_steps=5), resume_from=path)
        self.assertEqual(self.final()["step"], 5)


@unittest.skipUnless(torch.cuda.is_available(), "fp16 GradScaler training needs CUDA")
class TestFp16OnCuda(TrainingCase):
    def run_cuda(self, cfg, **kw):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            train(cfg, device_spec="cuda", **kw)
        return out.getvalue()

    def test_fp16_trains_saves_scaler_and_resumes(self):
        self.run_cuda(self.tiny(amp="fp16", max_steps=3, eval_interval=3))
        ckpt = self.final()
        self.assertIn("scale", ckpt["scaler_state"])
        self.assertTrue(torch.isfinite(torch.tensor(ckpt["val_loss"])))
        log = self.run_cuda(
            self.tiny(amp="fp16"), resume_from=os.path.join(self.ckpt_dir, "final.pt")
        )
        self.assertIn("restored GradScaler", log)
        self.assertEqual(self.final()["step"], 5)


if __name__ == "__main__":
    unittest.main()
