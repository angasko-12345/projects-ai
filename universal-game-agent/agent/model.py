"""Recurrent actor-critic: stacked frames -> action logits + state value.

image (B, C, 84, 84) -> small CNN -> feature vector -> GRU -> actor head
(logits over the configured action space) + critic head (scalar value).
Game-independent: only tensor shapes matter, never game internals.

Stage 2 options (all default to the legacy network, which is built with the
exact same modules, order and initialisation as before):

* ``recurrent=False``: frame-stack-only policy (CleanRL ``ppo_atari``): heads
  read the feature vector directly; ``hidden`` is accepted and returned
  unchanged so every caller keeps one API.
* ``encoder="impala"``: IMPALA-ResNet (16-32-32 conv sequences, CleanRL
  ``ppo_procgen``) instead of the Nature CNN.
* ``init="orthogonal"``: CleanRL ``layer_init`` (orthogonal sqrt 2, actor
  0.01, critic 1, zero bias) instead of the legacy Kaiming init.
* ``uint8`` observations (0..255) are divided by 255 in-model; float inputs
  are used as given (legacy pixels are already in [0, 1]).

Non-default options are recorded in ``_config`` (and so in checkpoints);
default ones are left out so legacy checkpoint payloads stay identical.
"""
from __future__ import annotations

import argparse

import math

import torch
import torch.nn as nn

ENCODERS = ("nature", "impala")
INITS = ("legacy", "orthogonal")


class _ResidualBlock(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        self.conv0 = nn.Conv2d(channels, channels, kernel_size=3, padding=1)
        self.conv1 = nn.Conv2d(channels, channels, kernel_size=3, padding=1)

    def forward(self, x):
        return x + self.conv1(torch.relu(self.conv0(torch.relu(x))))


class _ConvSequence(nn.Module):
    """IMPALA block: conv3x3 -> maxpool3x3/2 -> 2 residual blocks."""

    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1)
        self.pool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.res0 = _ResidualBlock(out_channels)
        self.res1 = _ResidualBlock(out_channels)

    def forward(self, x):
        return self.res1(self.res0(self.pool(self.conv(x))))


def _impala_cnn(in_channels: int) -> nn.Sequential:
    layers, c = [], in_channels
    for out in (16, 32, 32):
        layers.append(_ConvSequence(c, out))
        c = out
    return nn.Sequential(*layers, nn.Flatten(), nn.ReLU())


class ActorCritic(nn.Module):
    def __init__(
        self,
        num_actions: int = 3,
        in_channels: int = 4,
        frame_size: int = 84,
        feature_dim: int = 256,
        hidden_size: int = 128,
        num_layers: int = 1,
        recurrent: bool = True,
        encoder: str = "nature",
        init: str = "legacy",
    ):
        super().__init__()
        if not isinstance(recurrent, bool):
            raise ValueError(f"recurrent must be a bool, got {recurrent!r}")
        if encoder not in ENCODERS:
            raise ValueError(f"encoder must be one of {ENCODERS}, got {encoder!r}")
        if init not in INITS:
            raise ValueError(f"init must be one of {INITS}, got {init!r}")
        for name, value in (
            ("num_actions", num_actions),
            ("in_channels", in_channels),
            ("frame_size", frame_size),
            ("feature_dim", feature_dim),
            ("hidden_size", hidden_size),
            ("num_layers", num_layers),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive int, got {value!r}")
        self._config = {
            "num_actions": num_actions,
            "in_channels": in_channels,
            "frame_size": frame_size,
            "feature_dim": feature_dim,
            "hidden_size": hidden_size,
            "num_layers": num_layers,
        }
        # Only non-default options enter the config, so legacy payloads are unchanged.
        for key, value, default in (("recurrent", recurrent, True), ("encoder", encoder, "nature"),
                                    ("init", init, "legacy")):
            if value != default:
                self._config[key] = value
        self.recurrent = recurrent
        if encoder == "impala":
            self.cnn = _impala_cnn(in_channels)
        else:
            self.cnn = nn.Sequential(
                nn.Conv2d(in_channels, 32, kernel_size=8, stride=4),
                nn.ReLU(),
                nn.Conv2d(32, 64, kernel_size=4, stride=2),
                nn.ReLU(),
                nn.Conv2d(64, 64, kernel_size=3, stride=1),
                nn.ReLU(),
                nn.Flatten(),
            )
        with torch.no_grad():  # size the head for any frame_size
            conv_out = self.cnn(torch.zeros(1, in_channels, frame_size, frame_size)).shape[1]
        self.fc = nn.Sequential(nn.Linear(conv_out, feature_dim), nn.ReLU())
        head_in = hidden_size
        if recurrent:
            self.gru = nn.GRU(feature_dim, hidden_size, num_layers=num_layers, batch_first=True)
        else:
            self.gru = None
            head_in = feature_dim
        self.actor = nn.Linear(head_in, num_actions)
        self.critic = nn.Linear(head_in, 1)
        if init == "orthogonal":
            self._init_orthogonal()
        else:
            self._init_weights()

    def _init_orthogonal(self) -> None:
        """CleanRL ``layer_init``: orthogonal(sqrt 2) + zero bias; actor 0.01, critic 1."""
        for mod in self.modules():
            if isinstance(mod, (nn.Conv2d, nn.Linear)):
                nn.init.orthogonal_(mod.weight, math.sqrt(2))
                nn.init.zeros_(mod.bias)
        if self.gru is not None:
            for name, param in self.gru.named_parameters():
                nn.init.orthogonal_(param) if "weight" in name else nn.init.zeros_(param)
        nn.init.orthogonal_(self.actor.weight, gain=0.01)
        nn.init.zeros_(self.actor.bias)
        nn.init.orthogonal_(self.critic.weight, gain=1.0)
        nn.init.zeros_(self.critic.bias)

    def _init_weights(self) -> None:
        for mod in self.cnn.modules():
            if isinstance(mod, nn.Conv2d):
                nn.init.kaiming_normal_(mod.weight, nonlinearity="relu")
                nn.init.zeros_(mod.bias)
        nn.init.kaiming_normal_(self.fc[0].weight, nonlinearity="relu")
        nn.init.zeros_(self.fc[0].bias)
        if self.gru is not None:
            for name, param in self.gru.named_parameters():
                nn.init.orthogonal_(param) if "weight" in name else nn.init.zeros_(param)
        nn.init.orthogonal_(self.actor.weight, gain=0.01)
        nn.init.zeros_(self.actor.bias)
        nn.init.orthogonal_(self.critic.weight, gain=1.0)
        nn.init.zeros_(self.critic.bias)

    def initial_state(self, batch_size: int, device=None) -> torch.Tensor:
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size!r}")
        device = torch.device(device) if device is not None else next(self.parameters()).device
        return torch.zeros(self._config["num_layers"], batch_size, self._config["hidden_size"], device=device)

    def forward(self, obs: torch.Tensor, hidden: torch.Tensor):
        cfg = self._config
        if not isinstance(obs, torch.Tensor) or obs.dim() != 4:
            raise ValueError(f"obs must be a 4D (B,C,H,W) tensor, got {type(obs)}")
        if obs.shape[1] != cfg["in_channels"] or obs.shape[2] != cfg["frame_size"] or obs.shape[3] != cfg["frame_size"]:
            raise ValueError(
                f"obs must be (B,{cfg['in_channels']},{cfg['frame_size']},{cfg['frame_size']}), "
                f"got {tuple(obs.shape)}"
            )
        if not isinstance(hidden, torch.Tensor) or hidden.shape != (
            cfg["num_layers"],
            obs.shape[0],
            cfg["hidden_size"],
        ):
            raise ValueError(
                f"hidden must be ({cfg['num_layers']},{obs.shape[0]},{cfg['hidden_size']}), "
                f"got {tuple(hidden.shape) if isinstance(hidden, torch.Tensor) else hidden!r}"
            )
        feat = self.encode(obs)
        if self.gru is None:
            return self.actor(feat), self.critic(feat).squeeze(-1), hidden
        out, new_hidden = self.gru(feat.unsqueeze(1), hidden)
        last = out.squeeze(1)
        return self.actor(last), self.critic(last).squeeze(-1), new_hidden

    def encode(self, obs: torch.Tensor) -> torch.Tensor:
        """Pixels -> feature vector (B, feature_dim); uint8 input is scaled by 1/255."""
        x = obs.float() / 255.0 if obs.dtype == torch.uint8 else obs.float()
        return self.fc(self.cnn(x))

    def heads(self, core: torch.Tensor):
        """Core features (B, H) -> (logits (B, A), values (B,))."""
        return self.actor(core), self.critic(core).squeeze(-1)

    def forward_sequence(self, obs_seq: torch.Tensor, hidden: torch.Tensor):
        """Run a time-major chunk: obs_seq (T,C,H,W) -> logits (T,A), values (T,), hidden.

        Used by PPO updates so recurrence flows across the chunk; the
        returned hidden seeds the next chunk (detach between chunks).
        """
        cfg = self._config
        if not isinstance(obs_seq, torch.Tensor) or obs_seq.dim() != 4 or obs_seq.shape[0] < 1:
            raise ValueError(f"obs_seq must be a non-empty 4D (T,C,H,W) tensor, got {type(obs_seq)}")
        if (
            obs_seq.shape[1] != cfg["in_channels"]
            or obs_seq.shape[2] != cfg["frame_size"]
            or obs_seq.shape[3] != cfg["frame_size"]
        ):
            raise ValueError(
                f"obs_seq frames must be ({cfg['in_channels']},{cfg['frame_size']},{cfg['frame_size']}), "
                f"got {tuple(obs_seq.shape[1:])}"
            )
        if not isinstance(hidden, torch.Tensor) or hidden.shape != (cfg["num_layers"], 1, cfg["hidden_size"]):
            raise ValueError(
                f"hidden must be ({cfg['num_layers']},1,{cfg['hidden_size']}), "
                f"got {tuple(hidden.shape) if isinstance(hidden, torch.Tensor) else hidden!r}"
            )
        feat = self.encode(obs_seq)  # (T, F)
        if self.gru is None:
            return self.actor(feat), self.critic(feat).squeeze(-1), hidden
        out, new_hidden = self.gru(feat.unsqueeze(0), hidden)  # (1, T, H)
        last = out.squeeze(0)
        return self.actor(last), self.critic(last).squeeze(-1), new_hidden

    def save(self, path) -> None:
        torch.save({"config": self._config, "state": self.state_dict()}, path)

    @classmethod
    def load(cls, path, map_location="cpu") -> ActorCritic:
        ckpt = torch.load(path, map_location=map_location, weights_only=True)
        model = cls(**ckpt["config"])
        model.load_state_dict(ckpt["state"])
        return model


def run_demo(batch_size: int = 4, steps: int = 3) -> None:
    import time

    torch.manual_seed(0)
    model = ActorCritic().eval()
    hidden = model.initial_state(batch_size)
    with torch.no_grad():
        for t in range(steps):
            obs = torch.rand(batch_size, 4, 84, 84)
            start = time.perf_counter()
            logits, value, hidden = model(obs, hidden)
            ms = (time.perf_counter() - start) * 1000
            print(
                f"step {t}: obs={tuple(obs.shape)} logits={tuple(logits.shape)} "
                f"value={tuple(value.shape)} hidden={tuple(hidden.shape)} "
                f"({ms:.1f} ms CPU)"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Random-obs forward-pass demo")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--steps", type=int, default=3)
    args = parser.parse_args()
    run_demo(batch_size=args.batch_size, steps=args.steps)

# don't go insane don't go insane
