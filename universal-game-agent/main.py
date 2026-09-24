"""Universal Game Agent — thin command-line entry point.

Subcommands orchestrate existing components; all learning, evaluation,
and experiment logic lives in their modules (see README for the map).
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DEFAULT_CONFIG = str(ROOT / "configs" / "default.yaml")


def check_dependencies() -> dict[str, str]:
    status = {}
    for mod, label in (
        ("torch", "torch"),
        ("gymnasium", "gymnasium"),
        ("mss", "mss"),
        ("yaml", "pyyaml"),
    ):
        spec = importlib.util.find_spec(mod)
        if spec is None:
            status[label] = "missing"
            continue
        try:
            m = importlib.import_module(mod)
            status[label] = getattr(m, "__version__", "installed")
        except Exception as exc:  # pragma: no cover - defensive
            status[label] = f"import-failed: {exc}"
    return status


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _load_config(path: str) -> tuple[dict | None, int | None]:
    """Load YAML config or return (None, exit-code) after printing why."""
    if not Path(path).is_file():
        return None, _fail(f"config file not found: {path} (--config PATH)")
    try:
        from configs import load_config

        cfg = load_config(path)
    except Exception as exc:
        return None, _fail(f"cannot parse config {path}: {exc}")
    if not isinstance(cfg, dict):
        return None, _fail(f"config {path} must contain a top-level mapping")
    return cfg, None


def _need_torch(command: str):
    try:
        import torch  # noqa: F401
    except ImportError:
        raise SystemExit(f"error: '{command}' needs torch (pip install -r requirements.txt)")


def _build_env_model(cfg: dict, base_seed: int):
    """Construct (make_env, model, num_actions) from a config mapping."""
    import numpy as np
    import torch

    from agent.model import ActorCritic
    from training.experiment import make_env_from_config

    torch.manual_seed(base_seed)
    np.random.seed(base_seed)
    make_env = make_env_from_config(cfg.get("env", {}))
    probe = make_env()
    num_actions = int(probe.action_space.n)
    probe.close()
    model = ActorCritic(num_actions=num_actions, **cfg.get("model", {}))
    return make_env, model, num_actions


def _build_curiosity(cfg: dict, num_actions: int):
    cur_cfg = cfg.get("curiosity") or {}
    if not cur_cfg.get("enabled", False):
        return None
    from training.curiosity import CuriosityConfig, CuriosityModule

    return CuriosityModule(num_actions=num_actions, config=CuriosityConfig.from_dict(cur_cfg))


def cmd_smoke_test(args) -> int:
    cfg, err = _load_config(args.config)
    if err is not None:
        return err
    _need_torch("smoke-test")
    import torch

    from training.evaluate import evaluate
    from training.ppo import PPOConfig, PPOTrainer

    print(f"config: {args.config}")
    deps = check_dependencies()
    for name in ("torch", "gymnasium", "pyyaml"):
        if deps.get(name) in (None, "missing"):
            return _fail(f"missing required dependency for smoke-test: {name}")
    try:
        make_env, model, _ = _build_env_model(cfg, base_seed=0)
    except (ValueError, RuntimeError, OSError) as exc:
        return _fail(f"cannot build environment: {exc}")

    env = make_env()  # env reset/step path
    obs, _ = env.reset(seed=0)
    _, _, terminated, truncated, _ = env.step(0)
    print(f"env: obs={tuple(obs.shape)} {obs.dtype} step ok "
          f"(terminated={terminated}, truncated={truncated})")

    from environment.preprocessing import FrameStack  # preprocess/stack path
    from environment.toy_pong import ToyPongEnv

    raw, _ = ToyPongEnv().reset(seed=0)
    env_cfg = cfg.get("env", {})
    stack = FrameStack(num_stack=int(env_cfg.get("num_stack", 4)), size=int(env_cfg.get("obs_size", 84)))
    stacked = stack.reset(raw)
    hidden = model.initial_state(1)  # forward path
    with torch.no_grad():
        logits, value, _ = model(torch.from_numpy(stacked).unsqueeze(0), hidden)
    print(f"model: logits={tuple(logits.shape)} value={tuple(value.shape)}")

    tiny = dict(cfg.get("ppo", {}), rollout_length=8, minibatch_size=8,
                update_epochs=1, total_timesteps=8)
    trainer = PPOTrainer(make_env(), model, PPOConfig.from_dict(tiny))
    buf, *_ = trainer.collect_rollout()  # rollout path
    stats = trainer.update(buf)  # PPO update path
    rep = evaluate(model, make_env, episodes=1, seeds=[0])  # eval path
    print(f"ppo: pg={stats['policy_loss']:.4f} vf={stats['value_loss']:.4f} "
          f"ent={stats['entropy']:.4f}")
    print(f"eval: 1 episode reward={rep['episode_rewards'][0]:.1f} "
          f"length={rep['episode_lengths'][0]}")
    print("smoke-test OK")
    return 0

def _sync_optimizer_lr(trainer, learning_rate: float) -> None:
    """Point a resumed optimizer at the resumed run's LR; keep momentum state."""
    for group in trainer.optimizer.param_groups:
        group["lr"] = float(learning_rate)


def cmd_train(args) -> int:
    cfg, err = _load_config(args.config)
    if err is not None:
        return err
    _need_torch("train")
    from training.experiment import make_env_from_config
    from training.ppo import PPOConfig, PPOTrainer


    ppo_kwargs = dict(cfg.get("ppo", {}))
    if args.timesteps is not None:
        ppo_kwargs["total_timesteps"] = args.timesteps
    if args.seed is not None:
        ppo_kwargs["seed"] = args.seed
    if args.checkpoint_dir is not None:
        ppo_kwargs["checkpoint_dir"] = args.checkpoint_dir
    try:
        ppo_config = PPOConfig.from_dict(ppo_kwargs)
    except (ValueError, TypeError) as exc:
        return _fail(f"invalid training configuration: {exc}")

    if args.resume is not None:
        if not Path(args.resume).is_file():
            return _fail(f"checkpoint not found: {args.resume} (--resume PATH)")
        try:
            trainer = PPOTrainer.load_checkpoint(args.resume, make_env_from_config(cfg.get("env", {}))())
        except (ValueError, RuntimeError, OSError) as exc:
            return _fail(f"cannot resume training: {exc}")
        trainer.config = ppo_config  # overrides (timesteps/seed/dir) apply to resumed run
        _sync_optimizer_lr(trainer, ppo_config.learning_rate)
        print(f"resumed from {args.resume} at step {trainer.num_timesteps}")
    else:
        seed = int(ppo_kwargs.get("seed", 0))
        try:
            make_env, model, num_actions = _build_env_model(cfg, seed)
        except (ValueError, RuntimeError, OSError) as exc:
            return _fail(f"cannot build environment: {exc}")
        trainer = PPOTrainer(make_env(), model, ppo_config,
                             curiosity=_build_curiosity(cfg, num_actions))
    history = trainer.train()
    print(f"train done: steps={trainer.num_timesteps} "
          f"mean_reward_100={history['mean_reward'][-1]:.2f} "
          f"episodes={history['episodes'][-1]} "
          f"checkpoint={Path(ppo_config.checkpoint_dir) / 'ppo_final.pt'}")
    return 0


def cmd_evaluate(args) -> int:
    cfg, err = _load_config(args.config)
    if err is not None:
        return err
    _need_torch("evaluate")
    from agent.model import ActorCritic
    from training.evaluate import evaluate
    from training.experiment import make_env_from_config

    try:
        episodes = args.episodes if args.episodes is not None else int(cfg.get("eval", {}).get("episodes", 20))
    except (ValueError, TypeError) as exc:
        return _fail(f"invalid eval.episodes value: {exc}")
    if episodes <= 0:
        return _fail(f"--episodes must be positive, got {args.episodes}")
    seed = args.seed if args.seed is not None else 0
    try:
        make_env = make_env_from_config(cfg.get("env", {}))
    except (ValueError, RuntimeError, OSError) as exc:
        return _fail(f"cannot build environment: {exc}")
    if args.checkpoint is not None:
        if not Path(args.checkpoint).is_file():
            return _fail(f"checkpoint not found: {args.checkpoint} (--checkpoint PATH)")
        try:
            from training.ppo import PPOTrainer

            model = PPOTrainer.load_checkpoint(args.checkpoint, make_env()).model
        except KeyError:
            model = ActorCritic.load(args.checkpoint)  # ActorCritic.save format
        print(f"loaded checkpoint {args.checkpoint}")
    else:
        probe = make_env()
        num_actions = int(probe.action_space.n)
        probe.close()
        import torch

        torch.manual_seed(seed)
        model = ActorCritic(num_actions=num_actions, **cfg.get("model", {}))
    rep = evaluate(model, make_env, episodes=episodes,
                   seeds=[seed * 1000 + i for i in range(episodes)],
                   greedy=not args.sampled)
    print(f"eval ({'sampled' if args.sampled else 'greedy'}, {episodes} episodes): "
          f"mean={rep['mean_reward']:.2f} std={rep['std_reward']:.2f} "
          f"min={rep['min_reward']:.1f} max={rep['max_reward']:.1f} "
          f"mean_length={rep['mean_length']:.1f}")
    return 0


def cmd_experiment(args) -> int:
    if not Path(args.config).is_file():
        return _fail(f"config file not found: {args.config} (--config PATH)")
    _need_torch("experiment")
    from training.experiment import run_experiment

    try:
        report = run_experiment(args.config)
    except (ValueError, KeyError, TypeError) as exc:
        return _fail(f"invalid experiment configuration: {exc}")
    print(f"experiment done: baseline={report['initial_mean_episode_reward']:.2f} "
          f"final_eval={report['final_eval_mean_reward']:.2f} "
          f"steps={report['training_steps']} fps={report['fps']:.0f}")
    return 0


def _positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive int, got {value!r}")
    return ivalue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Universal Game Agent: toy PPO training, evaluation, and experiments."
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p_smoke = sub.add_parser("smoke-test", help="fast end-to-end pipeline check (env, model, PPO update, eval)")
    p_smoke.add_argument("--config", default=DEFAULT_CONFIG)
    p_smoke.set_defaults(func=cmd_smoke_test)

    p_train = sub.add_parser("train", help="train the actor-critic with PPO")
    p_train.add_argument("--config", default=DEFAULT_CONFIG)
    p_train.add_argument("--timesteps", type=_positive_int, default=None,
                         help="override ppo.total_timesteps")
    p_train.add_argument("--seed", type=int, default=None, help="override ppo.seed")
    p_train.add_argument("--checkpoint-dir", default=None, help="override ppo.checkpoint_dir")
    p_train.add_argument("--resume", default=None, metavar="PATH",
                         help="resume from an existing checkpoint file")
    p_train.set_defaults(func=cmd_train)

    p_eval = sub.add_parser("evaluate", help="evaluate a model (fresh or checkpoint) with fixed seeds")
    p_eval.add_argument("--config", default=DEFAULT_CONFIG)
    p_eval.add_argument("--checkpoint", default=None, metavar="PATH",
                        help="evaluate this checkpoint instead of a fresh model")
    p_eval.add_argument("--episodes", type=_positive_int, default=None,
                        help="override eval.episodes")
    p_eval.add_argument("--seed", type=int, default=None, help="seed base for eval episode seeds")
    p_eval.add_argument("--sampled", action="store_true",
                        help="sample actions instead of greedy argmax")
    p_eval.set_defaults(func=cmd_evaluate)

    p_exp = sub.add_parser("experiment", help="run a baseline -> train -> eval experiment YAML")
    p_exp.add_argument("--config", required=True, help="experiment YAML (see experiments/)")
    p_exp.set_defaults(func=cmd_experiment)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
