"""SFT plumbing shared by the LFM3 training kernel and its CPU dry-run.

The dry-run imports this module so the thing that gets tested is the thing that
runs on the GPU. Anything duplicated between the two would be untested code in
the expensive job.
"""

from __future__ import annotations


def build_collator(tokenizer, max_len):
    """Right-pad a batch and keep labels/attention mask aligned.

    Right padding is required for causal training: left padding would put pad
    tokens ahead of the supervised span. The attention mask matters too -- without
    it the model attends to pad positions and the loss is quietly wrong on every
    batch that needs padding.
    """
    import torch

    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        pad_id = tokenizer.eos_token_id

    def collate(rows):
        wid = max(min(len(r["input_ids"]), max_len) for r in rows)
        input_ids, labels, attn = [], [], []
        for r in rows:
            ids, lab = r["input_ids"][:wid], r["labels"][:wid]
            pad = wid - len(ids)
            input_ids.append(list(ids) + [pad_id] * pad)
            labels.append(list(lab) + [-100] * pad)
            attn.append([1] * len(ids) + [0] * pad)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
            "attention_mask": torch.tensor(attn, dtype=torch.long),
        }

    return collate


def batch_loss(model, batch, device="cuda"):
    """Forward loss for one batch."""
    return float(model(
        input_ids=batch["input_ids"].to(device),
        labels=batch["labels"].to(device),
        attention_mask=batch["attention_mask"].to(device),
    ).loss)


def val_loss(model, batches, device="cuda", n=24):
    """Mean loss over the first n batches. Restores train mode afterwards."""
    import torch

    model.eval()
    total, count = 0.0, 0
    with torch.no_grad():
        for b in batches[:n]:
            total += batch_loss(model, b, device)
            count += 1
    model.train()
    return total / max(count, 1)


def attach_lora(model, r, alpha, dropout, target_modules):
    """Wrap a base model in LoRA once, explicitly.

    Doing this here rather than passing ``peft_config`` to successive trainers
    avoids re-wrapping an already-PEFT model, which is version-dependent.
    """
    from peft import LoraConfig, get_peft_model

    cfg = LoraConfig(
        r=r,
        lora_alpha=alpha,
        lora_dropout=dropout,
        target_modules=list(target_modules),
        task_type="CAUSAL_LM",
    )
    wrapped = get_peft_model(model, cfg)
    wrapped.enable_input_require_grads()  # gradient checkpointing over frozen embeddings
    return wrapped, cfg


def make_sft_config(**kw):
    """Build SFTConfig, tolerating the max_length / max_seq_length rename."""
    from trl import SFTConfig

    try:
        return SFTConfig(**kw)
    except TypeError as e:
        if "max_length" in str(e) and "max_length" in kw:
            kw["max_seq_length"] = kw.pop("max_length")
            return SFTConfig(**kw)
        raise


def make_sft_trainer(model, args, dataset, collator, tokenizer):
    """Build SFTTrainer, tolerating the tokenizer / processing_class rename."""
    from trl import SFTTrainer

    kw = dict(model=model, args=args, train_dataset=dataset,
              data_collator=collator, processing_class=tokenizer)
    try:
        return SFTTrainer(**kw)
    except TypeError as e:
        if "processing_class" not in str(e):
            raise
        kw["tokenizer"] = kw.pop("processing_class")
        return SFTTrainer(**kw)
