"""Prediction-error curiosity (ICM forward dynamics, no inverse model).

obs -> small CNN encoder -> features; MLP predicts next features from
(current features, one-hot action). Per-step MSE is the raw intrinsic
reward. A running-std normalizer + fixed scale keep it from overwhelming
extrinsic reward. Transitions INTO a reset (episode boundaries) are masked
to zero -- there is no valid next observation there.

Game-independent: only pixel tensors and action indices flow through.
"""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn


@dataclass
class CuriosityConfig:
    enabled: bool = False
    scale: float = 0.1
    lr: float = 2.5e-4
    feature_dim: int = 128
    forward_hidden: int = 256
    normalize: bool = True
    reward_clip: float = 5.0

    def __post_init__(self):
        if self.scale < 0 or self.lr < 0 or self.reward_clip <= 0:
            raise ValueError(f"bad curiosity config: {self!r}")
        for name in ("feature_dim", "forward_hidden"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_dict(cls, data: dict) -> "CuriosityConfig":
        import warnings

        known = {f for f in cls.__dataclass_fields__}
        for key in (data or {}):
            if key not in known:
                warnings.warn(f"curiosity: ignoring unknown config key {key!r}", UserWarning, stacklevel=3)
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


class RunningMeanStd:
    """Batch Welford variance tracker for reward normalization."""

    def __init__(self, eps: float = 1e-8):
        self.mean = 0.0
        self.var = 1.0
        self.count = 0
        self.eps = eps

    def update(self, batch: torch.Tensor) -> None:
        b = batch.detach().flatten().double()
        n = b.numel()
        if n == 0:
            return
        batch_mean, batch_var = b.mean().item(), b.var(unbiased=False).item()
        total = self.count + n
        delta = batch_mean - self.mean
        self.mean += delta * n / total
        self.var = (self.var * self.count + batch_var * n + delta * delta * self.count * n / total) / total
        self.count = total

    def normalize(self, x: torch.Tensor) -> torch.Tensor:
        return (x / (self.var + self.eps) ** 0.5).float()

    def state_dict(self) -> dict:
        return {"mean": self.mean, "var": self.var, "count": self.count}

    def load_state_dict(self, state: dict) -> None:
        self.mean, self.var, self.count = state["mean"], state["var"], state["count"]


class FeatureEncoder(nn.Module):
    """Small Nature-style CNN: (B, C, 84, 84) -> (B, feature_dim)."""

    def __init__(self, in_channels: int = 4, frame_size: int = 84, feature_dim: int = 128):
        super().__init__()
        self.cnn = nn.Sequential(
            nn.Conv2d(in_channels, 32, 8, stride=4), nn.ReLU(),
            nn.Conv2d(32, 64, 4, stride=2), nn.ReLU(),
            nn.Conv2d(64, 64, 3, stride=1), nn.ReLU(),
            nn.Flatten(),
        )
        with torch.no_grad():
            conv_out = self.cnn(torch.zeros(1, in_channels, frame_size, frame_size)).shape[1]
        self.head = nn.Linear(conv_out, feature_dim)
        nn.init.kaiming_normal_(self.head.weight, nonlinearity="relu")
        nn.init.zeros_(self.head.bias)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.head(self.cnn(obs.float()))


class ForwardModel(nn.Module):
    """Predict next features from (features, one-hot action)."""

    def __init__(self, feature_dim: int, num_actions: int, hidden: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(feature_dim + num_actions, hidden), nn.ReLU(),
            nn.Linear(hidden, feature_dim),
        )

    def forward(self, features: torch.Tensor, actions: torch.Tensor) -> torch.Tensor:
        one_hot = nn.functional.one_hot(actions.long(), num_classes=self.net[0].in_features - features.shape[-1])
        return self.net(torch.cat([features, one_hot.float()], dim=-1))


class CuriosityModule:
    def __init__(self, num_actions: int, in_channels: int = 4, frame_size: int = 84,
                 config: CuriosityConfig | None = None, device="cpu"):
        if num_actions <= 0:
            raise ValueError(f"num_actions must be positive, got {num_actions!r}")
        self.config = config or CuriosityConfig()
        self.device = torch.device(device)
        self.encoder = FeatureEncoder(in_channels, frame_size, self.config.feature_dim).to(self.device)
        self.forward_model = ForwardModel(self.config.feature_dim, num_actions, self.config.forward_hidden).to(self.device)
        self.optimizer = torch.optim.Adam(
            list(self.encoder.parameters()) + list(self.forward_model.parameters()), lr=self.config.lr
        )
        self.rms = RunningMeanStd()

    def raw_error(self, obs: torch.Tensor, actions: torch.Tensor, next_obs: torch.Tensor) -> torch.Tensor:
        """Per-transition MSE, no masking or normalization. All (T, ...) CPU/GPU agnostic."""
        with torch.no_grad():
            target = self.encoder(next_obs)
        pred = self.forward_model(self.encoder(obs), actions)
        return ((pred - target) ** 2).mean(dim=-1)

    @torch.no_grad()
    def intrinsic(self, obs, actions, next_obs, valid: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """(scaled, raw-mean): boundary steps forced to zero via valid mask."""
        raw = self.raw_error(obs.to(self.device), actions.to(self.device), next_obs.to(self.device)).cpu()
        self.rms.update(raw[valid > 0] if valid.sum() > 0 else raw[:0])
        normed = self.rms.normalize(raw) if self.config.normalize else raw
        scaled = torch.clamp(normed * self.config.scale, max=self.config.reward_clip) * valid.float()
        return scaled, raw

    def update(self, obs, actions, next_obs, valid: torch.Tensor) -> float:
        """One predictor step on valid transitions; returns MSE loss."""
        if valid.sum() == 0:
            return 0.0
        o, a, n = obs.to(self.device), actions.to(self.device), next_obs.to(self.device)
        target = self.encoder(n).detach()
        pred = self.forward_model(self.encoder(o), a)
        loss = (((pred - target) ** 2).mean(dim=-1) * valid.to(self.device).float()).sum() / valid.sum()
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        return loss.item()

    def state_dict(self) -> dict:
        return {
            "encoder": self.encoder.state_dict(),
            "forward": self.forward_model.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "rms": self.rms.state_dict(),
            "config": self.config.__dict__,
        }

    def load_state_dict(self, state: dict) -> None:
        self.encoder.load_state_dict(state["encoder"])
        self.forward_model.load_state_dict(state["forward"])
        self.optimizer.load_state_dict(state["optimizer"])
        self.rms.load_state_dict(state["rms"])
