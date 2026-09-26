# orchestrator.py
# Requirements: Python 3.12+ (works on 3.14)
# No pip installs needed — stdlib only

import subprocess
import json
import re
from pathlib import Path
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────

AGENTS_DIR = Path(r"D:/admin/code/projects/.agents/orchestrator")
TASKS_DIR  = AGENTS_DIR / "tasks"
OUTPUT_DIR = AGENTS_DIR / "outputs"
MEMORY_DIR = AGENTS_DIR / "memory"
LOG_FILE   = MEMORY_DIR / "orchestrator-log.md"

# Map task types to agent CLI commands
# Adjust these to match how you actually invoke each agent
AGENT_COMMANDS = {
    "spec":    ["omp", "--no-interactive"],
    "code":    ["pi"],
    "verify":  ["pi"],
    "search":  ["opencode"],
    "default": ["pi"],
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def log(message: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n## {now()}\n{message}\n")
    print(message)

def classify_task(task_text: str) -> str:
    """Simple keyword-based classifier. Extend as needed."""
    text = task_text.lower()
    if any(w in text for w in ["spec", "plan", "design", "requirements"]):
        return "spec"
    if any(w in text for w in ["verify", "test", "check", "validate"]):
        return "verify"
    if any(w in text for w in ["search", "find", "research", "look up"]):
        return "search"
    if any(w in text for w in ["code", "implement", "write", "build", "fix", "create"]):
        return "code"
    return "default"

def get_pending_tasks() -> list[Path]:
    """Find all task files marked as pending."""
    if not TASKS_DIR.exists():
        return []
    return sorted(
        p for p in TASKS_DIR.glob("*.md")
        if "status: pending" in p.read_text(encoding="utf-8", errors="ignore").lower()
    )

def mark_task(task_file: Path, status: str):
    """Update the status line in a task file."""
    text = task_file.read_text(encoding="utf-8")
    # Replace any existing status line
    text = re.sub(
        r"(?i)^status:.*$",
        f"status: {status}",
        text,
        flags=re.MULTILINE
    )
    if "status:" not in text.lower():
        text = f"status: {status}\n" + text
    task_file.write_text(text, encoding="utf-8")

def run_agent(agent_cmd: list[str], prompt: str, task_name: str) -> tuple[bool, str]:
    """
    Invoke an agent CLI with the prompt piped to stdin.
    Returns (success, output).
    """
    try:
        result = subprocess.run(
            agent_cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max per task
        )
        output = result.stdout.strip() or result.stderr.strip()
        success = result.returncode == 0
        return success, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT: agent did not respond within 5 minutes"
    except FileNotFoundError:
        return False, f"AGENT NOT FOUND: {agent_cmd[0]} is not installed or not in PATH"
    except Exception as e:
        return False, f"ERROR: {e}"

def save_output(task_name: str, agent: str, output: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{task_name}-{agent}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out_file.write_text(
        f"# Output: {task_name}\nagent: {agent}\ntime: {now()}\n\n---\n\n{output}",
        encoding="utf-8"
    )
    return out_file

# ── Main Loop ─────────────────────────────────────────────────────────────────

def run_once():
    tasks = get_pending_tasks()
    if not tasks:
        log("No pending tasks found.")
        return

    log(f"Found {len(tasks)} pending task(s).")

    for task_file in tasks:
        task_name = task_file.stem
        task_text = task_file.read_text(encoding="utf-8")

        log(f"Processing: {task_name}")
        mark_task(task_file, "in-progress")

        # Classify and pick agent
        task_type = classify_task(task_text)
        agent_cmd = AGENT_COMMANDS.get(task_type, AGENT_COMMANDS["default"])
        log(f"  type={task_type} → agent={agent_cmd[0]}")

        # Run
        success, output = run_agent(agent_cmd, task_text, task_name)

        # Save output
        out_file = save_output(task_name, agent_cmd[0], output)
        log(f"  output saved → {out_file}")

        # Update task status
        if success:
            mark_task(task_file, "done")
            log(f"  ✓ done")
        else:
            mark_task(task_file, "failed")
            log(f"  ✗ failed: {output[:200]}")

if __name__ == "__main__":
    run_once()