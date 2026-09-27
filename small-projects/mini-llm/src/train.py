"""Training loop: AdamW + linear warmup / cosine decay, clipping, checkpoints."""

from __future__ import annotations

import argparse
import math
import os
import random
import sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config, config_for_data
from src.dataset import build_dataloader
from src.model import from_config


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def lr_at_step(step: int, cfg: Config) -> float:
    """Linear warmup, then cosine decay to min_lr_ratio * base lr."""
    if step < cfg.warmup_steps:
        # step is 1-based, so warmup ramps base/warmup ... base over steps 1..warmup.
        return cfg.learning_rate * step / max(1, cfg.warmup_steps)
    progress = (step - cfg.warmup_steps) / max(1, cfg.max_steps - cfg.warmup_steps)
    progress = min(1.0, max(0.0, progress))
    cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
    min_lr = cfg.learning_rate * cfg.min_lr_ratio
    return min_lr + cosine * (cfg.learning_rate - min_lr)


def build_param_groups(model, weight_decay: float) -> list[dict]:
    """AdamW groups: decay only matrices, leave 1-D (LayerNorm scale, biases) alone."""
    decay, no_decay = [], []
    for _, param in model.named_parameters():
        if not param.requires_grad:
            continue
        (decay if param.dim() >= 2 else no_decay).append(param)
    return [
        {"params": decay, "weight_decay": weight_decay},
        {"params": no_decay, "weight_decay": 0.0},
    ]


def capture_rng_state() -> dict:
    """RNG state in weights_only-safe form (plain ints/lists, no pickled objects)."""
    np_name, np_keys, np_pos, np_has_gauss, np_cached = np.random.get_state()
    return {
        "torch": torch.get_rng_state(),
        "python": random.getstate(),
        "numpy": {
            "name": np_name,
            "keys": np_keys.tolist(),
            "pos": int(np_pos),
            "has_gauss": int(np_has_gauss),
            "cached_gaussian": float(np_cached),
        },
    }


def restore_rng_state(state: dict | None) -> None:
    if not state:
        return
    if "torch" in state:
        torch.set_rng_state(state["torch"].to(torch.uint8))
    if "python" in state:
        random.setstate(state["python"])
    n = state.get("numpy")
    if n:
        np.random.set_state((n["name"], np.array(n["keys"], dtype=np.uint32), n["pos"],
                             n["has_gauss"], n["cached_gaussian"]))


def save_checkpoint(path: str, model, optimizer, step: int, cfg: Config, lr: float | None = None,
                    extra: dict | None = None) -> None:
    # LR is set manually (warmup + cosine), so scheduler state is the schedule
    # config plus the current lr value.
    ckpt = {
        "model_state": model.state_dict(),
        "optimizer_state": optimizer.state_dict(),
        "scheduler_state": {
            "lr": lr,
            "warmup_steps": cfg.warmup_steps,
            "max_steps": cfg.max_steps,
            "learning_rate": cfg.learning_rate,
            "min_lr_ratio": cfg.min_lr_ratio,
        },
        "step": step,
        "config": cfg.to_dict(),
        "rng_state": capture_rng_state(),
    }
    if extra:
        ckpt.update(extra)
    torch.save(ckpt, path)


def read_checkpoint(path: str) -> dict:
    """Load a checkpoint without unpickling arbitrary Python objects.

    Our format is tensors plus plain containers, so it loads under weights_only;
    a file that needs custom classes is rejected rather than executed.
    """
    try:
        return torch.load(path, map_location="cpu", weights_only=True)
    except Exception as exc:  # noqa: BLE001 - re-raised with actionable context below
        raise ValueError(
            f"{path} is not a loadable mini-llm checkpoint (it may contain pickled "
            f"objects): {type(exc).__name__}: {exc}"
        ) from exc


def load_checkpoint(path: str, model, optimizer=None) -> dict:
    ckpt = read_checkpoint(path)
    model.load_state_dict(ckpt["model_state"])
    if optimizer is not None and "optimizer_state" in ckpt:
        optimizer.load_state_dict(ckpt["optimizer_state"])
    restore_rng_state(ckpt.get("rng_state"))
    return ckpt


def load_model(path: str):
    """Rebuild the model a checkpoint describes and load its weights. Returns (model, cfg)."""
    ckpt = read_checkpoint(path)
    cfg = Config.from_dict(ckpt["config"])
    model = from_config(cfg)
    model.load_state_dict(ckpt["model_state"])  # strict: a mismatch must not pass silently
    model.eval()
    return model, cfg


@torch.no_grad()
def evaluate(model, loader, batches: int, device: torch.device) -> float:
    model.eval()
    n_batches = len(loader)
    if n_batches == 0:
        raise ValueError("cannot evaluate on an empty loader")
    # Never cycle: re-reading a short val set would weight its batches more than
    # their share and report a loss that is not the corpus loss. Weight by target
    # tokens so a ragged final batch counts for the tokens it actually has.
    limit = min(batches, n_batches)
    total_loss, total_tokens = 0.0, 0
    for i, (x, y) in enumerate(loader):
        if i >= limit:
            break
        x, y = x.to(device), y.to(device)
        _, loss = model(x, y)
        n_tokens = y.numel()
        total_loss += loss.item() * n_tokens
        total_tokens += n_tokens
    model.train()
    return total_loss / max(1, total_tokens)


def train(cfg: Config, resume_from: str | None = None, start_step: int = 1,
          stride: int = 1) -> None:
    if start_step > cfg.max_steps:
        raise SystemExit(
            f"checkpoint is already at step {start_step - 1} of max_steps {cfg.max_steps}"
        )
    set_seed(cfg.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device}")

    model = from_config(cfg).to(device)
    n_params = model.count_parameters()
    print(f"parameters: {n_params / 1e6:.2f}M ({n_params})")

    # drop_last=False for val so a one-sample val set still yields a batch.
    train_loader = build_dataloader(cfg.train_bin, cfg.context_length, cfg.batch_size,
                                    shuffle=True, stride=stride)
    val_loader = build_dataloader(cfg.val_bin, cfg.context_length, cfg.batch_size, shuffle=False)

    optimizer = torch.optim.AdamW(
        build_param_groups(model, cfg.weight_decay),
        lr=cfg.learning_rate,
        betas=(cfg.beta1, cfg.beta2),
        weight_decay=cfg.weight_decay,
    )
    os.makedirs(cfg.checkpoint_dir, exist_ok=True)

    if resume_from:
        ckpt = load_checkpoint(resume_from, model, optimizer)
        # The LR schedule is a pure function of the step number, so continuing from
        # the checkpoint's step continues the original warmup/cosine curve.
        print(f"resumed {resume_from} at step {ckpt['step']} -> next {ckpt['step'] + 1}")
        print(f"lr at resume: {lr_at_step(ckpt['step'] + 1, cfg):.6f}")

    model.train()
    train_iter = iter(train_loader)  # build_dataloader guarantees >= 1 full batch
    last_train_loss = last_val_loss = float("nan")
    for step in range(start_step, cfg.max_steps + 1):
        try:
            x, y = next(train_iter)
        except StopIteration:
            # Recover cleanly at end of epoch: reshuffle and continue.
            train_iter = iter(train_loader)
            x, y = next(train_iter)
        x, y = x.to(device), y.to(device)

        lr = lr_at_step(step, cfg)
        for group in optimizer.param_groups:
            group["lr"] = lr

        optimizer.zero_grad()
        _, loss = model(x, y)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
        optimizer.step()

        if step % 10 == 0 or step == 1:
            print(
                f"step {step}/{cfg.max_steps} "
                f"train_loss {loss.item():.4f} lr {lr:.6f}",
                flush=True,
            )

        if step % cfg.eval_interval == 0 or step == cfg.max_steps:
            val_loss = evaluate(model, val_loader, cfg.eval_batches, device)
            last_train_loss, last_val_loss = loss.item(), val_loss
            print(
                f"step {step} train_loss {loss.item():.4f} "
                f"val_loss {val_loss:.4f} lr {lr:.6f}",
                flush=True,
            )
            save_checkpoint(
                os.path.join(cfg.checkpoint_dir, f"step_{step}.pt"),
                model, optimizer, step, cfg, lr=lr,
                extra={"train_loss": loss.item(), "val_loss": val_loss},
            )

    save_checkpoint(
        os.path.join(cfg.checkpoint_dir, "final.pt"), model, optimizer,
        cfg.max_steps, cfg, lr=lr_at_step(cfg.max_steps, cfg),
        extra={"train_loss": last_train_loss, "val_loss": last_val_loss},
    )
    print("saved final checkpoint")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train mini-llm GPT")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--context-length", type=int, default=None)
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--train-bin", default=None)
    parser.add_argument("--val-bin", default=None)
    parser.add_argument("--vocab-size", type=int, default=None)
    parser.add_argument("--resume", default=None, metavar="CHECKPOINT",
                        help="continue from step_N.pt / final.pt: model, optimizer, "
                             "step and the original lr schedule are all restored")
    parser.add_argument("--stride", type=int, default=1,
                        help="training window stride in tokens: 1 (default) supervises "
                             "every transition; context_length gives non-overlapping chunks")
    args = parser.parse_args()

    if args.resume:
        ckpt = read_checkpoint(args.resume)
        # The checkpoint owns the run's config, so an interrupted run continues on
        # the same schedule instead of silently restarting at step 1.
        cfg = Config.from_dict(ckpt["config"])
        start_step = int(ckpt["step"]) + 1
        resume_step = int(ckpt["step"])
    else:
        overrides = {}
        for field in ("train_bin", "val_bin", "checkpoint_dir"):
            if getattr(args, field) is not None:
                overrides[field] = getattr(args, field)
        if args.vocab_size is not None:
            overrides["vocab_size"] = args.vocab_size
        # vocab_size comes from the data unless overridden, so the model is never
        # built with classes the prepared data cannot produce.
        cfg = config_for_data(overrides.pop("train_bin", Config.train_bin), **overrides)
        start_step = 1
        resume_step = 0

    for field, value in (("max_steps", args.max_steps), ("batch_size", args.batch_size),
                         ("context_length", args.context_length),
                         ("checkpoint_dir", args.checkpoint_dir),
                         ("train_bin", args.train_bin), ("val_bin", args.val_bin),
                         ("vocab_size", args.vocab_size)):
        if value is not None:
            setattr(cfg, field, value)
    if cfg.warmup_steps > cfg.max_steps:
        print(f"warmup_steps {cfg.warmup_steps} > max_steps {cfg.max_steps}; shortening")
        cfg.warmup_steps = cfg.max_steps
    cfg.__post_init__()
    cfg.validate_against_data()
    if args.resume:
        print(f"resuming {args.resume}: step {resume_step} -> {start_step} of {cfg.max_steps}")
    train(cfg, resume_from=args.resume, start_step=start_step, stride=args.stride)


if __name__ == "__main__":
    main()
