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


def _init_weights(module: nn.Module) -> None:
    if isinstance(module, (nn.Linear, nn.Embedding)):
        nn.init.normal_(module.weight, mean=0.0, std=0.02)
        if isinstance(module, nn.Linear) and module.bias is not None:
            nn.init.zeros_(module.bias)


class CausalSelfAttention(nn.Module):
    """Single-head causal self-attention: Q = xWq, K = xWk, V = xWv."""

    def __init__(self, d_model: int, d_head: int, dropout: float = 0.0):
        super().__init__()
        self.wq = nn.Linear(d_model, d_head, bias=False)
        self.wk = nn.Linear(d_model, d_head, bias=False)
        self.wv = nn.Linear(d_model, d_head, bias=False)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        q = self.wq(x)
        k = self.wk(x)
        v = self.wv(x)
        scores = q @ k.transpose(-2, -1) / math.sqrt(q.size(-1))
        # mask is the lower triangle built once per block by MultiHeadAttention.
        scores = scores.masked_fill(~mask, float("-inf"))
        weights = F.softmax(scores, dim=-1)
        return self.drop(weights) @ v


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
        t = x.size(1)
        # Causal mask: token i attends only to positions <= i. One mask for all heads.
        mask = torch.tril(torch.ones(t, t, device=x.device, dtype=torch.bool))
        out = torch.cat([h(x, mask) for h in self.heads], dim=-1)
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
        # Tie after init so the shared tensor is drawn from the RNG once.
        self.lm_head.weight = self.wte.weight

    def forward(
        self, idx: torch.Tensor, targets: torch.Tensor | None = None
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        b, t = idx.shape
        assert t <= self.context_length, f"sequence {t} > context {self.context_length}"
        pos = torch.arange(t, device=idx.device)
        x = self.wte(idx) + self.wpe(pos)
        for block in self.blocks:
            x = block(x)
        x = self.ln_f(x)
        logits = self.lm_head(x)
        loss = None
        if targets is not None:
            loss = F.cross_entropy(logits.reshape(-1, logits.size(-1)), targets.reshape(-1))
        return logits, loss

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
