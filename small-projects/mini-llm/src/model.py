"""GPT-style causal Transformer implemented manually with PyTorch modules.

Pipeline per block (pre-norm):
    x = x + MultiHeadCausalAttention(LN(x))
    x = x + MLP(LN(x))
Then final LN + output projection tied to the token embedding.
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint

# cross_entropy's default target value that is excluded from the loss. Named so
# the chunked loss divides its sum by the same token count the plain loss uses.
_IGNORE_INDEX = -100


def _init_weights(module: nn.Module) -> None:
    if isinstance(module, (nn.Linear, nn.Embedding)):
        nn.init.normal_(module.weight, mean=0.0, std=0.02)
        if isinstance(module, nn.Linear) and module.bias is not None:
            nn.init.zeros_(module.bias)


class CausalSelfAttention(nn.Module):
    """Single-head causal self-attention: Q = xWq, K = xWk, V = xWv.

    The score, scale, causal mask and softmax are computed by
    scaled_dot_product_attention with is_causal=True, which applies the same
    lower-triangular mask and 1/sqrt(d_head) scale as the explicit
    q @ k^T / sqrt(d) + masked softmax it replaces. wq/wk/wv keep their names
    and shapes, so checkpoints saved by the earlier implementation load as-is.
    """

    def __init__(self, d_model: int, d_head: int, dropout: float = 0.0):
        super().__init__()
        self.wq = nn.Linear(d_model, d_head, bias=False)
        self.wk = nn.Linear(d_model, d_head, bias=False)
        self.wv = nn.Linear(d_model, d_head, bias=False)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        q = self.wq(x)
        k = self.wk(x)
        v = self.wv(x)
        # Dropout is applied to the attention weights, matching self.drop(weights)
        # in the manual path; eval mode passes 0.0, so inference stays exact.
        return F.scaled_dot_product_attention(
            q, k, v,
            dropout_p=self.drop.p if self.training else 0.0,
            is_causal=True,
        )


class MultiHeadAttention(nn.Module):
    def __init__(self, d_model: int, n_heads: int, dropout: float = 0.0):
        super().__init__()
        assert d_model % n_heads == 0
        self.n_heads = n_heads
        self.d_head = d_model // n_heads
        self.heads = nn.ModuleList(
            [CausalSelfAttention(d_model, self.d_head, dropout) for _ in range(n_heads)]
        )
        self.proj = nn.Linear(d_model, d_model)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # The causal mask is now built inside SDPA per head, so it is no longer
        # rebuilt here once per block.
        out = torch.cat([h(x) for h in self.heads], dim=-1)
        return self.drop(self.proj(out))


class MLP(nn.Module):
    def __init__(self, d_model: int, d_ff: int, dropout: float = 0.0):
        super().__init__()
        self.fc1 = nn.Linear(d_model, d_ff)
        self.fc2 = nn.Linear(d_ff, d_model)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.drop(self.fc2(F.gelu(self.fc1(x))))


class Block(nn.Module):
    def __init__(self, d_model: int, n_heads: int, d_ff: int, dropout: float = 0.0):
        super().__init__()
        self.ln1 = nn.LayerNorm(d_model)
        self.attn = MultiHeadAttention(d_model, n_heads, dropout)
        self.ln2 = nn.LayerNorm(d_model)
        self.mlp = MLP(d_model, d_ff, dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.ln1(x))
        x = x + self.mlp(self.ln2(x))
        return x


class MiniGPT(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        context_length: int,
        n_layers: int,
        n_heads: int,
        d_model: int,
        d_ff: int,
        dropout: float = 0.0,
    ):
        super().__init__()
        assert d_model % n_heads == 0, "d_model must be divisible by n_heads"
        self.context_length = context_length
        self.wte = nn.Embedding(vocab_size, d_model)  # token embedding
        self.wpe = nn.Embedding(context_length, d_model)  # learned positions
        self.blocks = nn.ModuleList(
            [Block(d_model, n_heads, d_ff, dropout) for _ in range(n_layers)]
        )
        self.ln_f = nn.LayerNorm(d_model)
        self.lm_head = nn.Linear(d_model, vocab_size, bias=False)
        self.apply(_init_weights)
        # GPT-2 residual scaling: each block adds two branches to the stream, so
        # their output projections start at 0.02 / sqrt(2 * n_layers) and the
        # residual variance stays roughly constant as depth grows.
        residual_std = 0.02 / math.sqrt(2 * n_layers)
        for block in self.blocks:
            for proj in (block.attn.proj, block.mlp.fc2):
                nn.init.normal_(proj.weight, mean=0.0, std=residual_std)
        # Tie after init so the shared tensor is drawn from the RNG once.
        self.lm_head.weight = self.wte.weight

    def hidden_states(self, idx: torch.Tensor) -> torch.Tensor:
        """Final layer-norm output before the output projection: (B, T, d_model)."""
        t = idx.size(1)
        assert t <= self.context_length, f"sequence {t} > context {self.context_length}"
        pos = torch.arange(t, device=idx.device)
        x = self.wte(idx) + self.wpe(pos)
        for block in self.blocks:
            x = block(x)
        return self.ln_f(x)

    def forward(
        self, idx: torch.Tensor, targets: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        logits = self.lm_head(self.hidden_states(idx))
        loss = None
        if targets is not None:
            loss = F.cross_entropy(logits.reshape(-1, logits.size(-1)), targets.reshape(-1))
        return logits, loss

    def chunked_loss(
        self, idx: torch.Tensor, targets: torch.Tensor, chunk_size: int
    ) -> torch.Tensor:
        """Mean cross-entropy over all target tokens, computed chunk by chunk.

        Only one (B, chunk_size, vocab) logits tensor is live at a time: each
        chunk's forward is recomputed during backward, so the full (B, T, vocab)
        logits and their gradient are never materialized. The sum is divided by
        the same token count the plain loss uses, so targets the default
        ignore_index (-100) excludes cancel out and the result matches
        F.cross_entropy over the whole batch.
        """
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size!r}")
        hidden = self.hidden_states(idx)
        vocab = self.lm_head.out_features
        counted = int((targets != _IGNORE_INDEX).sum())

        def chunk_cross_entropy(h_chunk, t_chunk):
            logits = self.lm_head(h_chunk)
            return F.cross_entropy(
                logits.reshape(-1, vocab), t_chunk.reshape(-1),
                ignore_index=_IGNORE_INDEX, reduction="sum",
            )

        loss_sum = 0.0
        t = hidden.size(1)
        for start in range(0, t, chunk_size):
            end = min(start + chunk_size, t)
            loss_sum = loss_sum + checkpoint(
                chunk_cross_entropy, hidden[:, start:end, :], targets[:, start:end],
                use_reentrant=False,
            )
        return loss_sum / max(counted, 1)

    def count_parameters(self) -> int:
        # Tied lm_head shares storage with wte; count unique params only.
        seen: set[int] = set()
        total = 0
        for p in self.parameters():
            if id(p) not in seen:
                seen.add(id(p))
                total += p.numel()
        return total


def from_config(cfg) -> MiniGPT:
    return MiniGPT(
        vocab_size=cfg.vocab_size,
        context_length=cfg.context_length,
        n_layers=cfg.n_layers,
        n_heads=cfg.n_heads,
        d_model=cfg.d_model,
        d_ff=cfg.d_ff,
        dropout=cfg.dropout,
    )
