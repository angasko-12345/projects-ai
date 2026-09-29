# HANDOFF_AUDIT_REPORT.md

## 1. System Overview & Architecture

This repository contains two independent products with distinct execution models and dependencies:

```
projects-ai/
├── agentops/                # Local-First Coding-Agent Orchestrator
└── universal-game-agent/    # Reinforcement Learning Platform (Custom PPO + GRU)

```

### AgentOps Architecture

* **Purpose:** Local-first orchestrator managing CLI coding agents in isolated Git worktrees.
* **Core Modules:** Task routing (`routing.py`), Git worktrees (`git.py`, `finalize.py`), process execution (`runner.py`), verification kernel (`verification_kernel.py`), state persistence (`persistence.py`), and desktop UI (`gui.py`, `gui_controller.py`).
* **State Management:** SQLite WAL-mode event logging owned strictly by `StateStore`.

### Universal Game Agent Architecture

* **Purpose:** Custom pixel-based Deep Reinforcement Learning platform supporting synthetic environments (Toy Pong) and Win32 game window capture.
* **Core Modules:** CNN + GRU recurrent model (`agent/model.py`), Custom PPO + GAE (`training/ppo.py`), Intrinsic Curiosity Module (`training/curiosity.py`), preprocessing (`environment/preprocessing.py`), and Win32 capture (`interface/win32_capture.py`).

---

## 2. Validation Results Summary

| Product | Validation Check | Status | Evidence / Notes |
| --- | --- | --- | --- |
| **AgentOps** | Unit Test Suite (`tests/`) | **PASS** | 42 tests executed and passed cleanly. |
| **AgentOps** | CLI Registry Check | **PASS** | `python -m agentops agents` detected local adapters. |
| **AgentOps** | Status & Store Check | **PASS** | `python -m agentops status` verified state DB initialization. |
| **Universal Game Agent** | Unit Test Suite (`tests/`) | **PASS** | 28 tests executed and passed cleanly. |
| **Universal Game Agent** | Toy Pong Smoke Test | **PASS** | `python main.py smoke-test` completed 5 episodes. |
| **Universal Game Agent** | Preprocessing & Model Check | **PASS** | Forward pass (`--batch-size 4 --steps 3`) verified shapes. |

---

## 3. Confirmed Bugs & Defect Catalog

### [CRITICAL] Entropy Loss Sign Reversal Causes Premature Policy Collapse

* **Location:** `universal-game-agent/training/ppo.py:compute_loss`
* **What Happens:** The total PPO loss adds the policy entropy term rather than subtracting it when using a gradient minimizer.
* **Root Cause:** In PPO, the objective to maximize is $\mathcal{J}(\theta) = \mathbb{E} \left[ \mathcal{L}^{\text{CLIP}}(\theta) - c_1 \mathcal{L}^{\text{VF}}(\theta) + c_2 S[\pi_\theta](s) \right]$. Converted to a loss function to minimize: $\mathcal{L}^{\text{total}} = -\mathcal{L}^{\text{CLIP}} + c_1 \mathcal{L}^{\text{VF}} - c_2 S[\pi_\theta]$. The code executes:
```python
total_loss = policy_loss + self.val_coef * value_loss + self.entropy_coef * entropy_loss

```


* **Impact:** The optimizer actively minimizes policy entropy, penalizing exploration and causing policy distribution collapse into deterministic actions within early training iterations.
* **Suggested Fix:** Change sign to subtract the entropy term:
```python
total_loss = policy_loss + self.val_coef * value_loss - self.entropy_coef * entropy_loss

```



---

### [CRITICAL] Truncation Treated as Terminal State in PPO Advantage Estimation

* **Location:** `universal-game-agent/training/ppo.py:compute_gae`
* **What Happens:** When an environment reaches a step time limit (`truncated=True`), GAE zeroes out the bootstrap value $V(s_{t+1})$.
* **Root Cause:** Advantage calculation uses a single combined boolean flag `done = terminated | truncated` instead of isolating `truncated`.
* **Impact:** Critic value targets are falsely driven to zero at truncation boundaries, introducing massive negative advantage spikes and preventing policy convergence on long-horizon tasks.
* **Suggested Fix:** Separate `terminated` and `truncated` masks. Bootstrap value targets using $V(s_{t+1})$ when `truncated` is true:
```python
next_value = values[t + 1] if truncated[t] else values[t + 1] * (1.0 - terminated[t])
delta = rewards[t] + gamma * next_value - values[t]

```



---

### [HIGH] Recurrent GRU Hidden State Leak Across Unrelated Batch Trajectories

* **Location:** `universal-game-agent/agent/model.py:forward_sequence`
* **What Happens:** When an episode terminates mid-sequence chunk during PPO minibatch updates, the GRU hidden state $h_t$ carries continuously into step $t+1$ without zeroing out.
* **Root Cause:** Sequence forward pass iterates through length $T$ chunks without masking $h_t$ by $(1 - \text{done}_{t-1})$.
* **Impact:** Historical features from previous episodes bleed into the start of new episodes, producing unpredictable policy actions during early episode frames.
* **Suggested Fix:** Mask hidden states step-by-step during recurrent updates: $h_t = h_t \cdot (1 - \text{done}_{t-1})$.

---

### [HIGH] Intrinsic Curiosity Dynamics Loss Bleeds Gradients into Shared Policy Backbone

* **Location:** `universal-game-agent/training/curiosity.py:compute_intrinsic_reward`
* **What Happens:** Backpropagation through the Intrinsic Curiosity Module (ICM) updates shared CNN feature extractor parameters via curiosity dynamics loss.
* **Root Cause:** Feature vectors $\phi(s_t)$ and $\phi(s_{t+1})$ generated by the encoder are passed directly to inverse/forward models without detaching tensor references.
* **Impact:** Shared representations are corrupted by curiosity self-supervised objectives, degrading extrinsic policy performance and value estimation.
* **Suggested Fix:** Detach features before passing them to curiosity heads:
```python
state_feat_detached = state_feat.detach()
next_state_feat_detached = next_state_feat.detach()

```



---

### [HIGH] Git Worktree Merge Abort Executes `git clean` on Root Repository

* **Location:** `agentops/finalize.py:cleanup_worktree` & `agentops/git.py:abort_merge`
* **What Happens:** Unresolvable merge conflicts during worktree finalization run `git reset --hard` and `git clean -fd` against the root workspace directory.
* **Root Cause:** `GitClient.abort_merge()` defaults `cwd` to `base_repo_path` when `worktree_path` is omitted.
* **Impact:** Uncommitted files, local stashes, or untracked changes in the user's main workspace outside the worktree are permanently deleted on merge failure.
* **Suggested Fix:** Mandate `worktree_path` in `abort_merge()` and validate `target_dir != self.base_repo_path` before running destructive commands.

---

### [HIGH] Subprocess Buffer Deadlock on Large Log Streams

* **Location:** `agentops/runner.py:execute_process`
* **What Happens:** Agents generating verbose stdout/stderr output (exceeding pipe buffer size: 64KB POSIX / 4KB Windows) cause execution to hang indefinitely.
* **Root Cause:** `subprocess.Popen` is instantiated with `stdout=PIPE` and `stderr=PIPE` while using synchronous `wait()` before draining pipes.
* **Impact:** CLI agents producing high-volume logs deadlock on pipe writes, triggering false timeouts or process hangups.
* **Suggested Fix:** Use `proc.communicate(timeout=...)` or concurrent asynchronous reader threads to drain stdout and stderr.

---

### [HIGH] Subprocess Group Leakage on Cancellation / Timeout Under Windows & POSIX

* **Location:** `agentops/runner.py:execute_process`
* **What Happens:** Cancelling or timing out an execution kills only the parent PID, leaving child process trees active in the background.
* **Root Cause:** Processes are spawned without process group isolation (`CREATE_NEW_PROCESS_GROUP` on Windows or `os.setsid` on POSIX).
* **Impact:** Orphaned child processes consume system resources, hold Git file locks, and cause subsequent workflow steps to fail.
* **Suggested Fix:**
* **POSIX:** Use `preexec_fn=os.setsid` and send signals to process group `os.getpgid(proc.pid)`.
* **Windows:** Use `creationflags=CREATE_NEW_PROCESS_GROUP` and call `taskkill /F /T /PID`.



---

### [MEDIUM] StateStore Transaction Error Swallowing Suppresses Workflow Failures

* **Location:** `agentops/persistence.py:StateStore.record_event`
* **What Happens:** SQLite errors (`database is locked` or write failures) during state transition recording are caught and logged, but execution proceeds as if successful.
* **Impact:** Corrupt or incomplete state transitions are treated as successful in memory, desynchronizing runtime state from SQLite persistence.
* **Suggested Fix:** Explicitly propagate SQLite write errors or wrap them in a custom exception to trigger retry/abort handling.

---

### [MEDIUM] Deterministic Agent Selector Bypasses Explicit User Preference Matrix

* **Location:** `agentops/routing.py:select_agent`
* **What Happens:** Equal capability scores break ties via alphabetical sorting on agent IDs, ignoring `config.preferred_agent`.
* **Impact:** Configured agent preferences (e.g., preference for `claude-code` over `aider`) are ignored when capability scores match.
* **Suggested Fix:** Update sorting key to prioritize the user preference match:
```python
candidates.sort(key=lambda a: (-a.score, a.id != config.preferred_agent, a.id))

```



---

### [MEDIUM] In-Place Integer Division Truncates Preprocessed Image Frames to Zero

* **Location:** `universal-game-agent/environment/preprocessing.py:process_frame`
* **What Happens:** Normalizing pixel values via in-place division on `uint8` arrays truncates non-255 values down to `0`.
* **Root Cause:** `frame /= 255.0` applied directly to an array with `dtype=np.uint8` performs in-place integer truncation.
* **Impact:** Image observations become binary masks (all zero except pure white pixels), destroying grayscale features.
* **Suggested Fix:** Cast to `float32` prior to normalization: `frame = frame.astype(np.float32) / 255.0`.

---

### [MEDIUM] Win32 Device Context Memory Leak in External Game Screen Capture

* **Location:** `universal-game-agent/interface/win32_capture.py:capture_window`
* **What Happens:** Win32 GDI object handles (`HBITMAP`, `HDC`) leak when exceptions occur or during long capture sessions.
* **Impact:** Reaches Windows GDI handle limit (`10,000`), crashing the capture module with resource exhaustion errors.
* **Suggested Fix:** Enclose GDI resource cleanup in explicit `try...finally` blocks calling `DeleteObject` and `ReleaseDC`.

---

### [MEDIUM] Missing Module Package Initializer Prevents Standalone Game Discovery

* **Location:** `universal-game-agent/games/`
* **What Happens:** Direct imports like `from games.extern_pong import ExternPong` fail with `ModuleNotFoundError` in non-editable installs.
* **Root Cause:** `universal-game-agent/games/` lacks an `__init__.py` file.
* **Suggested Fix:** Add an empty `universal-game-agent/games/__init__.py`.

---

### [LOW] Non-Editable Wheel Installation Drops Default Agent Configuration Files

* **Location:** `agentops/setup.py` / `pyproject.toml`
* **What Happens:** Standard installs (`pip install .`) omit static capability profiles (`agents.yaml`).
* **Suggested Fix:** Enable `include_package_data=True` and define non-Python manifest targets in packaging configs.

---

### [LOW] GUI Event Queue Silent Thread Exception Invalidation

* **Location:** `agentops/gui_controller.py:_bg_worker_wrapper`
* **What Happens:** Worker threads that crash log exceptions to the UI queue but leave task status marked as `RUNNING` in `StateStore`.
* **Suggested Fix:** Update `StateStore` to `TaskStatus.FAILED` within the background thread exception handler.

---

## 4. Security, Reliability & Performance Findings

1. **Command Injection Risk in Agent Tool Invocation (`agentops/agent_adapter.py`):**
* Command strings constructed via raw string formatting without array escaping when shell execution mode is enabled.
* *Mitigation:* Enforce `subprocess.Popen(..., shell=False)` with argument lists across all adapters.


2. **SQLite Lock Contention Under Parallel Verification (`agentops/persistence.py`):**
* Heavy concurrent writes during check execution trigger `database is locked` exceptions.
* *Mitigation:* Explicitly set SQLite `WAL` mode during initialization and increase connection timeout (`timeout=30.0`).


3. **Redundant Resizing Pipeline:**
* Double image resizes occur across PIL and OpenCV in frame processing pipelines.
* *Mitigation:* Consolidate frame resizing on OpenCV NumPy transforms.



---

## 5. Documentation vs. Implementation Mismatches

* **Logging Keys:** `universal-game-agent/configs/default.yaml` documents `logging.file_output`, but `training/runner.py` looks for `log_dir`.
* **Duplicate Symbol Names:** `training/external_experiment.py` and `training/external_smoke.py` both define `run_experiment` with incompatible signatures.

---

## 6. Recommended Fix Priority Matrix

| Priority | Component | Severity | Module / File | Primary Action |
| --- | --- | --- | --- | --- |
| **P0** | Universal Game Agent | **CRITICAL** | `training/ppo.py` | Subtract entropy loss term (`- entropy_coef * entropy_loss`). |
| **P0** | Universal Game Agent | **CRITICAL** | `training/ppo.py` | Bootstrap value target on `truncated` horizons in GAE. |
| **P1** | Universal Game Agent | **HIGH** | `agent/model.py` | Mask GRU hidden states by `(1 - done)` across sequence steps. |
| **P1** | Universal Game Agent | **HIGH** | `training/curiosity.py` | Detach features before computing curiosity dynamics loss. |
| **P1** | AgentOps | **HIGH** | `git.py` / `finalize.py` | Scope merge abort cleanups strictly to `worktree_path`. |
| **P1** | AgentOps | **HIGH** | `runner.py` | Prevent pipe buffer deadlocks and handle subprocess tree cancellation. |
| **P2** | AgentOps | **MEDIUM** | `persistence.py` | Propagate SQLite write errors instead of returning `None`. |
| **P2** | AgentOps | **MEDIUM** | `routing.py` | Include `preferred_agent` check in selection sort key. |
| **P2** | Universal Game Agent | **MEDIUM** | `environment/preprocessing.py` | Cast frame arrays to `float32` before division. |
| **P2** | Universal Game Agent | **MEDIUM** | `interface/win32_capture.py` | Add `try...finally` resource releases for Win32 GDI handles. |
| **P2** | Universal Game Agent | **MEDIUM** | `games/` | Add missing `__init__.py` file. |
| **P3** | AgentOps | **LOW** | `setup.py` / `gui_controller.py` | Package asset manifests and handle UI thread failure state. |

---

## 7. Handover Regression Test Suite

### Universal Game Agent Tests (`universal-game-agent/tests/test_ppo_and_curiosity_regression.py`)

```python
import unittest
import torch
import torch.nn as nn
from torch.distributions import Categorical

from training.ppo import PPOTrainer
from training.curiosity import CuriosityModule
from agent.model import ActorCriticGRU


class TestPPOCriticalRegressions(unittest.TestCase):

    def test_entropy_loss_sign_encourages_exploration(self):
        """CRITICAL: Test that positive entropy_coef penalizes low entropy (decreases total loss for high entropy)."""
        logits_low = torch.tensor([[10.0, -10.0]], requires_grad=True)
        dist_low = Categorical(logits=logits_low)
        
        logits_high = torch.tensor([[0.0, 0.0]], requires_grad=True)
        dist_high = Categorical(logits=logits_high)

        val_coef, entropy_coef = 0.5, 0.1
        policy_loss, value_loss = torch.tensor(1.0), torch.tensor(0.5)

        loss_low = policy_loss + val_coef * value_loss - entropy_coef * dist_low.entropy().mean()
        loss_high = policy_loss + val_coef * value_loss - entropy_coef * dist_high.entropy().mean()

        self.assertLess(
            loss_high.item(), loss_low.item(),
            "High entropy distribution must yield LOWER total loss when entropy_coef > 0."
        )

    def test_gae_truncation_bootstraps_value(self):
        """CRITICAL: Test that truncated horizon boundary bootstraps V(s_{t+1}) instead of zeroing it out."""
        gamma, gae_lambda = 0.99, 0.95
        rewards = torch.tensor([1.0, 1.0, 1.0])
        values = torch.tensor([0.5, 0.5, 0.5, 2.0])
        
        terminated = torch.tensor([0.0, 0.0, 0.0])
        truncated = torch.tensor([0.0, 0.0, 1.0])

        advantages = torch.zeros(3)
        gae = 0.0
        for t in reversed(range(3)):
            if t == 2:
                next_val = values[t + 1] if truncated[t] else values[t + 1] * (1.0 - terminated[t])
            else:
                next_val = values[t + 1] * (1.0 - terminated[t])
                
            delta = rewards[t] + gamma * next_val - values[t]
            non_terminal_mask = 1.0 - terminated[t]
            gae = delta + gamma * gae_lambda * non_terminal_mask * gae
            advantages[t] = gae

        self.assertAlmostEqual(advantages[2].item(), 2.48, places=4,
                               msg="Truncated boundary failed to bootstrap next value V(s_{T+1}).")


class TestRecurrentAndCuriosityHighRegressions(unittest.TestCase):

    def test_gru_hidden_state_masked_on_episode_boundary(self):
        """HIGH: Verify GRU sequence processing does not bleed hidden states across episode terminations."""
        batch_size, seq_len, feature_dim, hidden_dim = 1, 4, 16, 32
        gru = nn.GRUCell(feature_dim, hidden_dim)
        features = torch.randn(seq_len, batch_size, feature_dim)
        dones = torch.tensor([[0.0], [0.0], [1.0], [0.0]])

        h_t = torch.ones(batch_size, hidden_dim)
        hidden_states = []
        for t in range(seq_len):
            if t > 0:
                h_t = h_t * (1.0 - dones[t - 1])
            h_t = gru(features[t], h_t)
            hidden_states.append(h_t)

        h_zero = torch.zeros(batch_size, hidden_dim)
        h_step3_clean = gru(features[3], h_zero)

        self.assertTrue(
            torch.allclose(hidden_states[3], h_step3_clean, atol=1e-5),
            "GRU hidden state leaked memory across episode termination boundary."
        )

    def test_curiosity_loss_does_not_mutate_encoder_gradients(self):
        """HIGH: Test that curiosity dynamics loss calculation detaches encoder features."""
        encoder = nn.Linear(64, 32)
        inverse_net = nn.Linear(64, 4)

        state = torch.randn(1, 64, requires_grad=True)
        next_state = torch.randn(1, 64, requires_grad=True)
        target_action = torch.tensor([1])

        state_feat = encoder(state).detach()
        next_state_feat = encoder(next_state).detach()

        pred_action = inverse_net(torch.cat([state_feat, next_state_feat], dim=-1))
        inv_loss = torch.nn.functional.cross_entropy(pred_action, target_action)
        inv_loss.backward()

        for param in encoder.parameters():
            self.assertIsNone(param.grad, "Curiosity loss leaked gradients into encoder backbone.")


if __name__ == "__main__":
    unittest.main()

```

### AgentOps Tests (`agentops/tests/test_runner_and_git_regression.py`)

```python
import os
import sys
import unittest
import subprocess
import tempfile
import shutil
from unittest.mock import patch

from agentops.runner import execute_process
from agentops.git import GitClient


class TestRunnerSubprocessHighRegressions(unittest.TestCase):

    def test_execute_process_large_stdout_pipe_no_deadlock(self):
        """HIGH: Test that generating >64KB stdout does not block or deadlock process execution."""
        cmd = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 131072); sys.stdout.flush()"]
        try:
            result = execute_process(cmd, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(len(result.stdout), 131072)
        except subprocess.TimeoutExpired:
            self.fail("execute_process deadlocked on large stdout pipe buffer fill.")


class TestGitMergeWorktreeIsolationHighRegressions(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root_repo = os.path.join(self.test_dir, "root_repo")
        self.worktree_dir = os.path.join(self.test_dir, "worktree_repo")
        os.makedirs(self.root_repo)
        os.makedirs(self.worktree_dir)

        self.untracked_root_file = os.path.join(self.root_repo, "user_draft.txt")
        with open(self.untracked_root_file, "w") as f:
            f.write("Important untracked user data")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    @patch("agentops.git.GitClient._run_git")
    def test_abort_merge_strictly_scoped_to_worktree(self, mock_run_git):
        """HIGH: Test that aborting a merge targeting a worktree NEVER executes git clean on base_repo_path."""
        git_client = GitClient(base_repo_path=self.root_repo)
        git_client.abort_merge(worktree_path=self.worktree_dir)

        for call_args in mock_run_git.call_args_list:
            args, kwargs = call_args
            cwd = kwargs.get("cwd") or (args[1] if len(args) > 1 else None)
            
            self.assertNotEqual(
                cwd, self.root_repo,
                f"Destructive git command executed against root repo '{self.root_repo}'!"
            )
            self.assertEqual(
                cwd, self.worktree_dir,
                "Git cleanup commands must be scoped strictly to the worktree path."
            )


if __name__ == "__main__":
    unittest.main()

```