"""Training loop: AdamW + linear warmup / cosine decay, clipping, checkpoints."""

from __future__ import annotations

import argparse
import math
import os
import random
import re
import sys
import tempfile
import time

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config, config_for_data, file_sha256, provenance_mismatches
from src.dataset import build_dataloader
from src.model import from_config

# Checkpoint format versions.
#
# Version 1 is the original layout: a bare `config` dict, and nothing that says
# which fields the format requires. Version 2 adds `checkpoint_metadata`, a
# self-describing block naming the schema version and repeating what the file
# needs in order to be read back: the model shape, the tokenizer by path and
# digest, and the training progress. Readers then check the file instead of
# assuming it, and a checkpoint that cannot describe itself is refused rather
# than completed from defaults.
CHECKPOINT_SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1

# The model fields from_config() reads: the shape and behavior needed to rebuild
# the model without the surrounding Config.
MODEL_FIELDS = ("vocab_size", "context_length", "n_layers", "n_heads", "d_model",
                "d_ff", "dropout")

# Top-level keys a checkpoint cannot be read without. rng_state is deliberately
# not among them: checkpoints written before the RNG was saved still load, they
# just continue the RNG stream instead of restoring it.
REQUIRED_CHECKPOINT_KEYS = ("model_state", "config", "step")

# Marker read_checkpoint() stamps on the dict it hands back. Passing that same
# dict to load_model()/load_checkpoint() skips a second validation pass, so the
# in-tree callers still read each checkpoint file exactly once. A dict from
# anywhere else has no marker and is validated on the way in, so passing a
# dictionary is not a way around validate_checkpoint(). read_checkpoint() always
# validates what it reads, so a marker that reached a file is never trusted:
# only the in-memory dict this process validated carries it.
VALIDATED_KEY = "_mini_llm_validated"


def build_checkpoint_metadata(cfg: Config, step: int, *, epoch: float | None = None,
                              tokens_seen: int | None = None,
                              provenance: dict | None = None) -> dict:
    """The block stored at a checkpoint's `checkpoint_metadata` key.

    Plain strings, numbers and dicts only, so a checkpoint carrying it still
    loads under weights_only=True. The same facts are readable in `config` and
    `data_provenance`; repeating them here is what lets one block say what the
    file is, so a reader can verify it against the rest of the checkpoint.
    """
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "model": {name: getattr(cfg, name) for name in MODEL_FIELDS},
        "tokenizer": {
            "path": cfg.tokenizer_path,
            # Absent only when the run had no verifiable data set (no meta.json
            # beside the .bin files). The path still identifies the tokenizer;
            # the digest, which identifies its contents, is what can be lost.
            "sha256": (provenance or {}).get("tokenizer_sha256"),
        },
        "progress": {
            "step": int(step),
            "epoch": epoch,
            "tokens_seen": tokens_seen,
            "max_steps": int(cfg.max_steps),
        },
        "required_keys": list(REQUIRED_CHECKPOINT_KEYS),
    }


def _metadata_gaps(meta: dict) -> list[str]:
    """Fields a self-describing block must carry to describe its run."""
    gaps = []
    model = meta.get("model")
    if not isinstance(model, dict):
        gaps.append("model")
    else:
        gaps += [f"model.{name}" for name in MODEL_FIELDS if name not in model]
    tokenizer = meta.get("tokenizer")
    if not isinstance(tokenizer, dict) or not tokenizer.get("path"):
        gaps.append("tokenizer.path")
    progress = meta.get("progress")
    if not isinstance(progress, dict) or progress.get("step") is None:
        gaps.append("progress.step")
    return gaps


def _legacy_metadata(ckpt: dict, path: str) -> dict:
    """Metadata for a checkpoint written before the block existed.

    Schema 1 kept the same facts in `config`, so a config that still carries them
    describes the run and is read as it stands. What is refused is a checkpoint
    that would have to be completed by guessing: a missing `config`, or one
    without the model shape or the tokenizer, because a defaulted vocab_size or
    context_length describes a different model than these weights belong to.
    """
    config = ckpt.get("config")
    gaps = [key for key in REQUIRED_CHECKPOINT_KEYS if key not in ckpt]
    if not isinstance(config, dict):
        gaps.append("config")
    else:
        gaps += [name for name in MODEL_FIELDS + ("tokenizer_path",)
                 if name not in config]
    if gaps:
        raise ValueError(
            f"{path} has no checkpoint metadata and records no {', '.join(gaps)}; "
            "the model shape and tokenizer it was trained with are unknown and must "
            "not be guessed from defaults - start a new run instead of loading it"
        )
    return {
        "schema_version": LEGACY_SCHEMA_VERSION,
        "model": {name: config[name] for name in MODEL_FIELDS},
        "tokenizer": {"path": config["tokenizer_path"], "sha256": None},
        "progress": {"step": int(ckpt["step"]), "epoch": None, "tokens_seen": None,
                     "max_steps": config.get("max_steps")},
        "required_keys": list(REQUIRED_CHECKPOINT_KEYS),
    }


def validate_checkpoint(ckpt: dict, path: str) -> dict:
    """Check a checkpoint can describe its own setup; return its metadata.

    Raises ValueError naming what is wrong. Refusing here is the point: a reader
    that filled in a default model shape or tokenizer would silently load a
    different setup than the one the checkpoint was written by.
    """
    if not isinstance(ckpt, dict):
        raise ValueError(
            f"{path} is not a mini-llm checkpoint: expected a dict of fields, got "
            f"{type(ckpt).__name__}"
        )
    meta = ckpt.get("checkpoint_metadata")
    if meta is None:
        meta = _legacy_metadata(ckpt, path)
    elif not isinstance(meta, dict):
        raise ValueError(f"{path} has checkpoint_metadata of type "
                         f"{type(meta).__name__}, not a dict of fields")
    else:
        version = meta.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError(
                f"{path} records checkpoint_metadata with no readable schema_version "
                f"(got {version!r}); the file's format is unknown, so its fields "
                "cannot be trusted to mean what this build expects"
            )
        if version != CHECKPOINT_SCHEMA_VERSION:
            origin = ("a newer mini-llm" if version > CHECKPOINT_SCHEMA_VERSION
                      else "an older checkpoint format")
            raise ValueError(
                f"{path} uses checkpoint schema {version}, written by {origin}; this "
                f"build reads schema {CHECKPOINT_SCHEMA_VERSION}. Use the checkout that "
                "wrote it, or start a new run."
            )

    missing = [key for key in REQUIRED_CHECKPOINT_KEYS if key not in ckpt]
    gaps = _metadata_gaps(meta)
    if missing or gaps:
        problems = []
        if missing:
            problems.append(f"no {', '.join(missing)}")
        if gaps:
            problems.append(f"checkpoint_metadata without {', '.join(gaps)}")
        raise ValueError(
            f"{path} is not a usable mini-llm checkpoint: {'; '.join(problems)}. The "
            "run it belongs to cannot be identified from the file, and the model "
            "shape and tokenizer must not be guessed - start a new run."
        )

    # `config` is what the loaders actually rebuild the model from, so it has to
    # agree with the metadata block. A disagreement means one of the two was
    # edited after the checkpoint was written, and reading either one alone builds
    # a model the other does not describe.
    config = ckpt["config"]
    if not isinstance(config, dict):
        raise ValueError(f"{path} has a config of type {type(config).__name__}, "
                         "not a dict of settings")
    model_state = ckpt["model_state"]
    if not isinstance(model_state, dict):
        raise ValueError(f"{path} has a model_state of type {type(model_state).__name__}, "
                         "not a dict of tensors")
    stale = []
    for name in MODEL_FIELDS:
        if name not in config:
            stale.append(f"{name}: metadata={meta['model'][name]!r} config=<missing>")
        elif config[name] != meta["model"][name]:
            stale.append(f"{name}: metadata={meta['model'][name]!r} "
                         f"config={config[name]!r}")

    # Progress step vs checkpoint step
    meta_step = meta.get("progress", {}).get("step")
    ckpt_step = ckpt.get("step")
    if meta_step is not None and ckpt_step is not None and meta_step != ckpt_step:
        stale.append(f"step: metadata={meta_step!r} checkpoint={ckpt_step!r}")

    # Tokenizer path vs config tokenizer_path
    meta_tok_path = meta.get("tokenizer", {}).get("path")
    cfg_tok_path = config.get("tokenizer_path")
    if meta_tok_path is not None and cfg_tok_path is not None and meta_tok_path != cfg_tok_path:
        stale.append(f"tokenizer_path: metadata={meta_tok_path!r} config={cfg_tok_path!r}")

    # Progress max_steps vs config max_steps (when both exist)
    meta_max_steps = meta.get("progress", {}).get("max_steps")
    cfg_max_steps = config.get("max_steps")
    if meta_max_steps is not None and cfg_max_steps is not None and meta_max_steps != cfg_max_steps:
        stale.append(f"max_steps: metadata={meta_max_steps!r} config={cfg_max_steps!r}")

    # Metadata tokenizer sha256 vs data_provenance tokenizer_sha256 (when both exist)
    meta_tok_sha = meta.get("tokenizer", {}).get("sha256")
    prov = ckpt.get("data_provenance")
    prov_tok_sha = prov.get("tokenizer_sha256") if isinstance(prov, dict) else None
    if meta_tok_sha is not None and prov_tok_sha is not None and meta_tok_sha != prov_tok_sha:
        stale.append(f"tokenizer_sha256: metadata={meta_tok_sha!r} provenance={prov_tok_sha!r}")

    # Model tensor shapes vs model metadata
    model_meta = meta.get("model", {})
    wte = model_state.get("wte.weight")
    if hasattr(wte, "shape"):
        vocab_size = model_meta.get("vocab_size")
        d_model = model_meta.get("d_model")
        if vocab_size is not None and len(wte.shape) > 0 and wte.shape[0] != vocab_size:
            stale.append(f"wte.weight vocab_size: tensor={wte.shape[0]} metadata={vocab_size}")
        if d_model is not None and len(wte.shape) > 1 and wte.shape[1] != d_model:
            stale.append(f"wte.weight d_model: tensor={wte.shape[1]} metadata={d_model}")

    wpe = model_state.get("wpe.weight")
    if hasattr(wpe, "shape"):
        context_length = model_meta.get("context_length")
        d_model = model_meta.get("d_model")
        if context_length is not None and len(wpe.shape) > 0 and wpe.shape[0] != context_length:
            stale.append(f"wpe.weight context_length: tensor={wpe.shape[0]} metadata={context_length}")
        if d_model is not None and len(wpe.shape) > 1 and wpe.shape[1] != d_model:
            stale.append(f"wpe.weight d_model: tensor={wpe.shape[1]} metadata={d_model}")

    if stale:
        # Joined outside the f-string: a backslash inside an f-string expression is
        # a syntax error before Python 3.12, and 3.11 is the supported floor.
        listed = "\n  ".join(stale)
        raise ValueError(
            f"{path} is internally inconsistent: its checkpoint metadata contradicts "
            f"the checkpoint data or configuration.\n  {listed}\n"
            "One of the two was edited after the checkpoint was written; load an "
            "untouched checkpoint."
        )
    return meta


def configure_torch_threads(cfg: Config) -> None:
    """Apply optional CPU thread settings; a no-op when both are None.

    Must run before any parallel work starts (train() calls it first),
    because set_num_interop_threads() refuses to run afterwards. A late
    call degrades to a warning instead of killing the run.
    """
    if cfg.torch_threads is not None:
        torch.set_num_threads(cfg.torch_threads)
    if cfg.torch_interop_threads is not None:
        try:
            torch.set_num_interop_threads(cfg.torch_interop_threads)
        except RuntimeError as exc:
            print(f"warning: ignoring --torch-interop-threads "
                  f"{cfg.torch_interop_threads}: {exc}")
    print(f"torch threads: intraop={torch.get_num_threads()} "
          f"interop={torch.get_num_interop_threads()}", flush=True)


def maybe_compile_model(model, enabled: bool):
    """Wrap the model in torch.compile when requested, else return it as-is.

    Checkpoints keep pointing at the original module (same parameter
    objects), so compiled state-dict prefixes never leak into step_N.pt,
    and loading/generation never depend on compilation.
    """
    if not enabled:
        return model
    compile_fn = getattr(torch, "compile", None)
    if compile_fn is None:
        raise SystemExit(
            "torch.compile is unavailable in this PyTorch build; "
            "rerun without --compile"
        )
    try:
        compiled = compile_fn(model)
    except Exception as exc:
        raise SystemExit(f"torch.compile failed: {exc}; rerun without --compile") from exc
    try:
        # Backends compile lazily: a one-batch dry run forces Inductor etc.
        # to fail here with a clear message instead of mid-training. RNG
        # state is restored so the run is bit-identical with or without it.
        device = next(model.parameters()).device
        t = min(8, model.context_length)
        rng = torch.get_rng_state()
        with torch.no_grad():
            compiled(torch.zeros(1, t, dtype=torch.long, device=device))
        torch.set_rng_state(rng)
    except Exception as exc:
        raise SystemExit(
            f"torch.compile dry run failed ({type(exc).__name__}: {exc}); "
            f"rerun without --compile"
        ) from exc
    return compiled


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def resolve_device(spec: str) -> torch.device:
    """Map a --device spec to a torch.device.

    auto keeps the historical behavior (CUDA when available, else CPU). An
    explicit cuda must never fall back: a cloud run that asked for a GPU has
    to fail before spending a training budget on CPU.
    """
    if spec == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if spec == "cpu":
        return torch.device("cpu")
    if spec == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit(
                "--device cuda requested but CUDA is not available in this "
                "PyTorch build/runtime; use --device cpu or --device auto"
            )
        return torch.device("cuda")
    raise SystemExit(f"--device must be auto, cpu or cuda, got {spec!r}")


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
    cuda_states = state.get("cuda")
    # Saved on GPU, resumed elsewhere: restore only what this machine has,
    # never more devices than exist; CPU-only machines skip it (old checkpoints
    # have no "cuda" key and skip the same way).
    if cuda_states and torch.cuda.is_available():
        for i, s in enumerate(cuda_states):
            if i >= torch.cuda.device_count():
                break
            torch.cuda.set_rng_state(s.to(torch.uint8), i)


def _atomic_torch_save(obj, path: str) -> None:
    """Write a torch object so a reader sees either the old file or the new one.

    The bytes go to a temp file in the same directory first; only a save that
    ran to completion is moved onto the destination, and os.replace is atomic
    within one filesystem. If the save fails, the temp file is removed and
    whatever was already at path is left untouched, so a half-written file can
    never replace a good checkpoint.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory,
                               prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            torch.save(obj, handle)
            # Push the bytes to disk before the rename so a crash just after it
            # cannot leave a renamed but incomplete file.
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# Numbered training checkpoints. Rotation matches only this shape, so final.pt,
# hand-made files and atomic-save temp files are never candidates for deletion.
STEP_CHECKPOINT_RE = re.compile(r"^step_(\d+)\.pt$")


def list_step_checkpoints(checkpoint_dir: str) -> list[tuple[int, str]]:
    """Numbered training checkpoints as (step, path), oldest step first."""
    try:
        names = os.listdir(checkpoint_dir)
    except FileNotFoundError:
        return []
    found = []
    for name in names:
        match = STEP_CHECKPOINT_RE.match(name)
        if match:
            found.append((int(match.group(1)), os.path.join(checkpoint_dir, name)))
    found.sort(key=lambda pair: pair[0])
    return found


def rotate_checkpoints(checkpoint_dir: str, keep_last: int,
                       keep: str | None = None) -> list[str]:
    """Delete the oldest numbered checkpoints so at most keep_last remain.

    Call this only after a new checkpoint is already on disk: the newest is then
    always retained, and `keep` (the file just written) is protected even if its
    step number says otherwise. final.pt and any name that is not step_<n>.pt are
    never touched. Returns the removed paths, oldest first. A file that cannot be
    removed is reported and skipped rather than aborting training.
    """
    if keep_last < 1:
        raise ValueError(f"keep_last must be >= 1, got {keep_last!r}")
    existing = list_step_checkpoints(checkpoint_dir)
    protected = os.path.abspath(keep) if keep else None
    doomed = existing[:-keep_last] if len(existing) > keep_last else []
    removed = []
    for _, path in doomed:
        if protected is not None and os.path.abspath(path) == protected:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"warning: could not remove old checkpoint {path}: {exc}",
                  file=sys.stderr)
            continue
        removed.append(path)
    return removed


def save_checkpoint(path: str, model, optimizer, step: int, cfg: Config, lr: float | None = None,
                    extra: dict | None = None, progress: dict | None = None) -> None:
    # LR is set manually (warmup + cosine), so scheduler state is the schedule
    # config plus the current lr value.
    rng_state = capture_rng_state()
    # Dropout and any other GPU draw come from the CUDA generator, not the CPU
    # one; keyed off the model device so CPU checkpoints keep their old shape.
    if next(model.parameters()).device.type == "cuda":
        rng_state["cuda"] = torch.cuda.get_rng_state_all()
    progress = progress or {}
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
        "rng_state": rng_state,
    }
    # Which data/tokenizer set produced these weights, by content. Stored next to
    # the config because the config's paths are what the user typed, while these
    # digests are what a resume must be able to prove against the current artifacts.
    try:
        ckpt["data_provenance"] = cfg.data_provenance()
    except (ValueError, FileNotFoundError) as exc:
        # Saving is the wrong moment to discover a broken data set; train() and
        # main() already refuse to start on one. Record why it is unavailable
        # rather than writing a checkpoint that looks resumable.
        ckpt["data_provenance"] = None
        print(f"warning: no data provenance in {path}: {exc}", file=sys.stderr)
    # What this file is, in one block: schema version, model shape, tokenizer and
    # progress. Written last of the described fields so the digest above is the
    # one the metadata names.
    ckpt["checkpoint_metadata"] = build_checkpoint_metadata(
        cfg, step, epoch=progress.get("epoch"),
        tokens_seen=progress.get("tokens_seen"),
        provenance=ckpt["data_provenance"],
    )
    if extra:
        ckpt.update(extra)
    _atomic_torch_save(ckpt, path)


def read_checkpoint(path: str) -> dict:
    """Load and validate a checkpoint without unpickling arbitrary Python objects.

    Our format is tensors plus plain containers, so it loads under weights_only;
    a file that needs custom classes is rejected rather than executed. The
    checkpoint is then checked against its own metadata, so every caller either
    gets one that describes its setup or a ValueError naming what is missing. A
    checkpoint written before the metadata block existed comes back carrying the
    block its config implies, in memory only, so callers never branch on version.
    """
    try:
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
    except Exception as exc:  # noqa: BLE001 - re-raised with actionable context below
        raise ValueError(
            f"{path} is not a loadable mini-llm checkpoint (it may contain pickled "
            f"objects): {type(exc).__name__}: {exc}"
        ) from exc
    # A schema 1 checkpoint comes back carrying the metadata its config implies,
    # in memory only, so callers can read the same key either way.
    ckpt["checkpoint_metadata"] = validate_checkpoint(ckpt, path)
    ckpt[VALIDATED_KEY] = True
    return ckpt


def validated_checkpoint(checkpoint: str | dict) -> dict:
    """The checkpoint dict, guaranteed to have passed validate_checkpoint().

    Takes a path (read once from disk and validated) or an already-read dict.
    A dict read_checkpoint() validated carries the marker and is returned as-is;
    any other dict is validated here first, so no caller can hand the loaders an
    unchecked checkpoint. Validating is cheap and pure - it reads no files - so
    this adds no second read of the checkpoint.
    """
    if not isinstance(checkpoint, dict):
        return read_checkpoint(checkpoint)
    if checkpoint.get(VALIDATED_KEY) is not True:
        checkpoint["checkpoint_metadata"] = validate_checkpoint(
            checkpoint, "<in-memory checkpoint>")
        checkpoint[VALIDATED_KEY] = True
    return checkpoint


def load_checkpoint(checkpoint: str | dict, model, optimizer=None) -> dict:
    ckpt = validated_checkpoint(checkpoint)
    model.load_state_dict(ckpt["model_state"])
    if optimizer is not None and "optimizer_state" in ckpt:
        optimizer.load_state_dict(ckpt["optimizer_state"])
    restore_rng_state(ckpt.get("rng_state"))
    return ckpt


def load_model(checkpoint: str | dict):
    """Rebuild the model a checkpoint describes and load its weights. Returns (model, cfg)."""
    ckpt = validated_checkpoint(checkpoint)
    cfg = Config.from_dict(ckpt["config"])
    model = from_config(cfg)
    model.load_state_dict(ckpt["model_state"])  # strict: a mismatch must not pass silently
    model.eval()
    return model, cfg


def _chunked_loss(module, x, y, chunk_size):
    """Chunked loss on the underlying model, unwrapping torch.compile if present."""
    return getattr(module, "_orig_mod", module).chunked_loss(x, y, chunk_size)


@torch.no_grad()
def evaluate(model, loader, batches: int, device: torch.device,
             chunk_size: int = 0) -> float:
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
        if chunk_size > 0:
            loss = _chunked_loss(model, x, y, chunk_size)
        else:
            _, loss = model(x, y)
        n_tokens = y.numel()
        total_loss += loss.item() * n_tokens
        total_tokens += n_tokens
    model.train()
    return total_loss / max(1, total_tokens)


def _digest_or_missing(path: str) -> str:
    """sha256 of a file, or an explicit marker when it is absent.

    Only for building a mismatch report: a missing artifact is itself a reason
    not to resume, and "<missing>" compares unequal to any recorded digest.
    """
    try:
        return file_sha256(path)
    except OSError:
        return f"<missing: {path}>"


def check_resume_provenance(ckpt: dict, cfg: Config, checkpoint_path: str) -> None:
    """Refuse to resume against data/tokenizer the checkpoint was not trained on.

    A resumed run keeps the checkpoint's train/val/tokenizer paths unless flags
    override them, and any of those paths may now hold different artifacts than
    when the weights were written. Continuing would silently train a model on one
    corpus and generate its text with another tokenizer, so a mismatch in the
    recorded digests is a hard error naming both sides.
    """
    recorded = ckpt.get("data_provenance")
    try:
        current = cfg.data_provenance()
    except (ValueError, FileNotFoundError):
        # The config and the artifacts on disk already disagree. Report that in
        # digest terms against the checkpoint rather than stopping at whichever
        # internal check tripped first: the useful answer is "these are not the
        # files this checkpoint was trained on".
        current = {"vocab_size": cfg.vocab_size,
                   "train_sha256": _digest_or_missing(cfg.train_bin),
                   "val_sha256": _digest_or_missing(cfg.val_bin),
                   "tokenizer_sha256": _digest_or_missing(cfg.tokenizer_path)}
    mismatches = provenance_mismatches(recorded, current)
    if not mismatches:
        print(f"data matches {checkpoint_path}: train={current['train_sha256'][:12]}... "
              f"val={current['val_sha256'][:12]}... "
              f"tokenizer={current['tokenizer_sha256'][:12]}...")
        return
    # Joined outside the f-string: a backslash inside an f-string expression is
    # a syntax error before Python 3.12, and 3.11 is the supported floor.
    listed = "\n  ".join(mismatches)
    raise SystemExit(
        f"refusing to resume {checkpoint_path}: its data does not match the current "
        f"artifacts.\n  {listed}\n"
        f"  checkpoint data: train={recorded.get('train_bin') if recorded else 'unknown'} "
        f"tokenizer={recorded.get('tokenizer_path') if recorded else 'unknown'}\n"
        f"  current data:   train={cfg.train_bin} tokenizer={cfg.tokenizer_path}\n"
        "Pass the --train-bin/--val-bin/--tokenizer the run actually used, or start a "
        "new run instead of resuming."
    )


def train(cfg: Config, resume_from: str | None = None, start_step: int | None = None,
          stride: int = 1, device_spec: str = "auto") -> None:
    ckpt = None
    if resume_from:
        ckpt = read_checkpoint(resume_from)
        if start_step is None:
            start_step = int(ckpt["step"]) + 1
    elif start_step is None:
        start_step = 1

    if start_step > cfg.max_steps:
        raise SystemExit(
            f"checkpoint is already at step {start_step - 1} of max_steps {cfg.max_steps}"
        )
    # Validate the data/tokenizer set before spending any time on a model, so a
    # mismatched or stale artifact fails before the first step, not after it.
    try:
        provenance = cfg.data_provenance()
    except (ValueError, FileNotFoundError) as exc:
        # One failure type for the command line: the data set is not the one this
        # config describes. The cause is already an actionable message.
        raise SystemExit(f"data/tokenizer set is not consistent:\n  {exc}") from exc
    print(f"data: train={provenance['train_bin']} "
          f"(sha256 {provenance['train_sha256'][:12]}...)\n"
          f"      tokenizer={provenance['tokenizer_path']} "
          f"(sha256 {provenance['tokenizer_sha256'][:12]}...)")
    if resume_from:
        check_resume_provenance(ckpt, cfg, resume_from)
    configure_torch_threads(cfg)
    set_seed(cfg.seed)
    device = resolve_device(device_spec)
    print(f"device: {device}")

    model = from_config(cfg).to(device)
    # The original module owns params, optimizer state and checkpoints;
    # train_model is only the forward/backward path (possibly compiled).
    train_model = maybe_compile_model(model, cfg.compile)
    n_params = model.count_parameters()
    print(f"parameters: {n_params / 1e6:.2f}M ({n_params})")

    # drop_last=False for val so a one-sample val set still yields a batch.
    train_loader = build_dataloader(cfg.train_bin, cfg.context_length, cfg.batch_size,
                                    shuffle=True, stride=stride)
    val_loader = build_dataloader(cfg.val_bin, cfg.context_length, cfg.batch_size, shuffle=False)
    # An epoch is one pass over the corpus: tokens consumed divided by the tokens
    # the training file holds. Counting it in tokens rather than in batches keeps
    # it independent of --stride and --batch-size, so it means the same thing in a
    # resumed run as in the run that wrote the checkpoint.
    n_train_tokens = max(1, len(train_loader.dataset.tokens))

    optimizer = torch.optim.AdamW(
        build_param_groups(model, cfg.weight_decay),
        lr=cfg.learning_rate,
        betas=(cfg.beta1, cfg.beta2),
        weight_decay=cfg.weight_decay,
    )
    os.makedirs(cfg.checkpoint_dir, exist_ok=True)

    tokens_before = 0  # tokens a resumed run had already consumed
    if resume_from:
        ckpt = load_checkpoint(ckpt, model, optimizer)
        # The LR schedule is a pure function of the step number, so continuing from
        # the checkpoint's step continues the original warmup/cosine curve.
        print(f"resumed {resume_from} at step {ckpt['step']} -> next {ckpt['step'] + 1}")
        print(f"lr at resume: {lr_at_step(ckpt['step'] + 1, cfg):.6f}")
        # Carry the consumed-token count across the resume. This run's own
        # total_tokens starts at 0, so without this the epoch count of a
        # continuation would describe only the part after the resume.
        recorded = (ckpt.get("checkpoint_metadata") or {}).get("progress") or {}
        tokens_before = int(recorded.get("tokens_seen") or 0)

    def progress_snapshot() -> dict:
        """How far the run has read the corpus, for the checkpoint metadata."""
        seen = tokens_before + total_tokens
        return {"tokens_seen": seen, "epoch": seen / n_train_tokens}

    model.train()
    train_model.train()
    train_iter = iter(train_loader)  # build_dataloader guarantees >= 1 full batch
    last_train_loss = last_val_loss = float("nan")
    total_tokens = 0
    t_start = time.perf_counter()
    # Each loss is scaled by 1/accum before backward, so the accumulated gradient
    # is the mean over batch_size * accum examples; steps count optimizer updates.
    accum = cfg.grad_accum_steps
    for step in range(start_step, cfg.max_steps + 1):
        lr = lr_at_step(step, cfg)
        for group in optimizer.param_groups:
            group["lr"] = lr

        optimizer.zero_grad()
        group_loss = 0.0
        for _ in range(accum):
            try:
                x, y = next(train_iter)
            except StopIteration:
                # Recover cleanly at end of epoch: reshuffle and continue.
                train_iter = iter(train_loader)
                x, y = next(train_iter)
            x, y = x.to(device), y.to(device)
            total_tokens += x.numel()  # actual batch tokens, not batch_size * context
            if cfg.loss_chunk_size > 0:
                loss = _chunked_loss(train_model, x, y, cfg.loss_chunk_size)
            else:
                _, loss = train_model(x, y)
            (loss / accum).backward()
            group_loss += loss.item() / accum

        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
        optimizer.step()

        if step % 10 == 0 or step == 1:
            print(
                f"step {step}/{cfg.max_steps} "
                f"train_loss {group_loss:.4f} lr {lr:.6f}",
                flush=True,
            )

        if step % cfg.eval_interval == 0 or step == cfg.max_steps:
            val_loss = evaluate(train_model, val_loader, cfg.eval_batches, device,
                                cfg.loss_chunk_size)
            last_train_loss, last_val_loss = group_loss, val_loss
            print(
                f"step {step} train_loss {group_loss:.4f} "
                f"val_loss {val_loss:.4f} lr {lr:.6f}",
                flush=True,
            )
            checkpoint_path = os.path.join(cfg.checkpoint_dir, f"step_{step}.pt")
            save_checkpoint(
                checkpoint_path,
                model, optimizer, step, cfg, lr=lr, progress=progress_snapshot(),
                extra={"train_loss": group_loss, "val_loss": val_loss},
            )
            # Rotate only after the new checkpoint is safely on disk, so the file
            # just written can never be deleted and the newest always survives.
            for old_path in rotate_checkpoints(cfg.checkpoint_dir, cfg.keep_last,
                                               keep=checkpoint_path):
                print(f"removed old checkpoint {old_path}")

    save_checkpoint(
        os.path.join(cfg.checkpoint_dir, "final.pt"), model, optimizer,
        cfg.max_steps, cfg, lr=lr_at_step(cfg.max_steps, cfg),
        progress=progress_snapshot(),
        extra={"train_loss": last_train_loss, "val_loss": last_val_loss},
    )
    print("saved final checkpoint")
    elapsed = time.perf_counter() - t_start
    n_steps = cfg.max_steps - start_step + 1
    print(f"training time {elapsed:.1f}s over {n_steps} steps, "
          f"{n_steps / elapsed:.2f} steps/sec, "
          f"{total_tokens / elapsed:.0f} tokens/sec ({total_tokens} tokens)")


# Flags that map straight onto a Config field: (argument dest, Config field).
# Every one defaults to None and is written only when the flag was passed, so an
# unset flag cannot move a Config default. The dest and the field differ for
# --tokenizer, whose Config field is tokenizer_path. main() applies this same map
# on a fresh run and on a resume, so the two paths cannot drift apart.
CLI_CONFIG_FIELDS = (
    ("max_steps", "max_steps"),
    ("batch_size", "batch_size"),
    ("context_length", "context_length"),
    ("n_layers", "n_layers"),
    ("n_heads", "n_heads"),
    ("d_model", "d_model"),
    ("d_ff", "d_ff"),
    ("dropout", "dropout"),
    ("learning_rate", "learning_rate"),
    ("weight_decay", "weight_decay"),
    ("min_lr_ratio", "min_lr_ratio"),
    ("beta1", "beta1"),
    ("beta2", "beta2"),
    ("grad_clip", "grad_clip"),
    ("grad_accum_steps", "grad_accum_steps"),
    ("loss_chunk_size", "loss_chunk_size"),
    ("warmup_steps", "warmup_steps"),
    ("eval_interval", "eval_interval"),
    ("eval_batches", "eval_batches"),
    ("seed", "seed"),
    ("checkpoint_dir", "checkpoint_dir"),
    ("keep_last", "keep_last"),
    ("train_bin", "train_bin"),
    ("val_bin", "val_bin"),
    ("tokenizer", "tokenizer_path"),
    ("vocab_size", "vocab_size"),
    ("torch_threads", "torch_threads"),
    ("torch_interop_threads", "torch_interop_threads"),
)


def apply_cli_config(cfg: Config, args: argparse.Namespace) -> None:
    """Copy each passed CLI value onto cfg in place.

    A flag left at its None default is skipped, which is what keeps a bare
    invocation identical to the library defaults. Only the fields in
    CLI_CONFIG_FIELDS move, and only when the flag carries a value.
    """
    for dest, field in CLI_CONFIG_FIELDS:
        value = getattr(args, dest)
        if value is not None:
            setattr(cfg, field, value)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train mini-llm GPT")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--context-length", type=int, default=None)
    parser.add_argument("--checkpoint-dir", default=None)
    parser.add_argument("--keep-last", dest="keep_last", type=int, default=None,
                        help="numbered step_*.pt checkpoints to retain (default 3); "
                             "older ones are deleted after each new one is saved. "
                             "final.pt is always kept. Must be >= 1")
    parser.add_argument("--train-bin", default=None)
    parser.add_argument("--val-bin", default=None)
    parser.add_argument("--tokenizer", "--tokenizer-path", dest="tokenizer", default=None,
                        help="tokenizer the .bin files were encoded with; defaults to "
                             "data/tokenizer.json and is recorded in every checkpoint, "
                             "so a resume is checked against it (--tokenizer-path is the "
                             "same flag, named after the Config field)")
    parser.add_argument("--vocab-size", type=int, default=None)
    # Model size and training hyperparameters. Each defaults to None, so it only
    # changes the Config value when the flag is actually passed.
    parser.add_argument("--n-layers", dest="n_layers", type=int, default=None,
                        help="number of transformer blocks")
    parser.add_argument("--n-heads", dest="n_heads", type=int, default=None,
                        help="attention heads; --d-model must divide evenly by it")
    parser.add_argument("--d-model", dest="d_model", type=int, default=None,
                        help="model width (embedding size)")
    parser.add_argument("--d-ff", dest="d_ff", type=int, default=None,
                        help="feed-forward hidden width")
    parser.add_argument("--dropout", type=float, default=None,
                        help="dropout probability (default 0.0)")
    parser.add_argument("--learning-rate", dest="learning_rate", type=float, default=None,
                        help="peak AdamW learning rate")
    parser.add_argument("--weight-decay", dest="weight_decay", type=float, default=None,
                        help="AdamW weight decay on matrix parameters")
    parser.add_argument("--min-lr-ratio", dest="min_lr_ratio", type=float, default=None,
                        help="cosine floor as a fraction of the peak learning rate")
    parser.add_argument("--beta1", type=float, default=None, help="AdamW beta1")
    parser.add_argument("--beta2", type=float, default=None, help="AdamW beta2")
    parser.add_argument("--grad-clip", dest="grad_clip", type=float, default=None,
                        help="max global gradient norm")
    parser.add_argument("--grad-accum-steps", dest="grad_accum_steps", type=int,
                        default=None, metavar="N",
                        help="microbatches accumulated per optimizer update; the "
                             "effective batch is --batch-size * N. 1 (default) updates "
                             "on every batch. Must be >= 1")
    parser.add_argument("--loss-chunk-size", dest="loss_chunk_size", type=int,
                        default=None, metavar="N",
                        help="sequence positions per chunk of the LM loss, recomputed "
                             "in backward so full B x T x vocab logits are never live. "
                             "0 (default) disables chunking. Must be >= 0")
    parser.add_argument("--warmup-steps", dest="warmup_steps", type=int, default=None,
                        help="linear warmup length in steps")
    parser.add_argument("--eval-interval", dest="eval_interval", type=int, default=None,
                        help="steps between validation and checkpoints")
    parser.add_argument("--eval-batches", dest="eval_batches", type=int, default=None,
                        help="validation batches averaged per evaluation")
    parser.add_argument("--seed", type=int, default=None, help="random seed")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto",
                        help="training device: auto (default) picks CUDA when "
                             "available, else CPU; cuda fails instead of silently "
                             "falling back to CPU")
    parser.add_argument("--resume", default=None, metavar="CHECKPOINT",
                        help="continue from step_N.pt / final.pt: model, optimizer, "
                             "step and the original lr schedule are all restored")
    parser.add_argument("--stride", type=int, default=1,
                        help="training window stride in tokens: 1 (default) supervises "
                             "every transition; context_length gives non-overlapping chunks")
    parser.add_argument("--torch-threads", type=int, default=None, metavar="N",
                        help="torch.set_num_threads(N); omit for the PyTorch default")
    parser.add_argument("--torch-interop-threads", type=int, default=None, metavar="N",
                        help="torch.set_num_interop_threads(N); omit for the default")
    # default=None (not False) so a resumed run keeps the checkpoint's value
    # unless the flag is passed explicitly.
    parser.add_argument("--compile", dest="compile", action="store_true", default=None,
                        help="torch.compile the model before training (default off; "
                             "benchmark it, it is not known-good on Windows CPU)")
    return parser


def main() -> None:
    args = make_parser().parse_args()

    if args.resume:
        ckpt = read_checkpoint(args.resume)
        # The checkpoint owns the run's config, so an interrupted run continues on
        # the same schedule instead of silently restarting at step 1.
        cfg = Config.from_dict(ckpt["config"])
        start_step = int(ckpt["step"]) + 1
        resume_step = int(ckpt["step"])
    else:
        overrides = {}
        # The tokenizer flag is --tokenizer; the config field is tokenizer_path.
        for flag, field in (("train_bin", "train_bin"), ("val_bin", "val_bin"),
                            ("tokenizer", "tokenizer_path"),
                            ("checkpoint_dir", "checkpoint_dir")):
            if getattr(args, flag) is not None:
                overrides[field] = getattr(args, flag)
        if args.vocab_size is not None:
            overrides["vocab_size"] = args.vocab_size
        # vocab_size comes from the data unless overridden, so the model is never
        # built with classes the prepared data cannot produce.
        cfg = config_for_data(overrides.pop("train_bin", Config.train_bin), **overrides)
        start_step = 1
        resume_step = 0

    apply_cli_config(cfg, args)
    if args.compile is not None:
        cfg.compile = args.compile
    for name in ("torch_threads", "torch_interop_threads"):
        value = getattr(cfg, name)
        if value is not None and value < 1:
            make_parser().error(f"--{name.replace('_', '-')} must be >= 1, got {value}")
    if cfg.warmup_steps > cfg.max_steps:
        print(f"warmup_steps {cfg.warmup_steps} > max_steps {cfg.max_steps}; shortening")
        cfg.warmup_steps = cfg.max_steps
    try:
        cfg.__post_init__()
    except AssertionError as exc:
        # A shape the model cannot build (say d_model not divisible by n_heads)
        # is the user's flag choice, so report it as a usage error instead of an
        # AssertionError traceback.
        make_parser().error(str(exc))
    if args.resume:
        # Before validate_against_data(): a resume against other artifacts should be
        # reported as a resume mismatch, not as whichever internal check tripped first.
        check_resume_provenance(ckpt, cfg, args.resume)
    cfg.validate_against_data()
    if args.resume:
        print(f"resuming {args.resume}: step {resume_step} -> {start_step} of {cfg.max_steps}")
    train(cfg, resume_from=args.resume, start_step=start_step, stride=args.stride,
          device_spec=args.device)


if __name__ == "__main__":
    main()
