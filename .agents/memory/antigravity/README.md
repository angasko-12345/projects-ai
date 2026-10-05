# Antigravity Scoped Memory

Agent-scoped memory for Antigravity (Google DeepMind agentic coding assistant).

This directory follows the canonical repository layout documented in `.agents/AGENTS.md`. Universal shared knowledge lives in `.agents/memory/*.md`, while agent-specific operational memory, patterns, verified baselines, and session records live here.

## Authoritative Precedence

Nothing in this directory overrides canonical memory (`.agents/memory/project.md`, `decisions.md`, `lessons.md`, `architecture.md`, `roadmap.md`, or `.agents/AGENTS.md`). In any conflict:
1. Current source code
2. Current test results
3. Explicit user instructions
4. Canonical memory (`.agents/memory/*.md`)
5. Agent-scoped memory (`.agents/memory/antigravity/`)

## Contents

* [`antigravity_memory.md`](file:///D:/admin/code/projects/.agents/memory/antigravity/antigravity_memory.md): Main operational memory, project architecture, verified baselines, conventions, pitfalls, and session takeaways.
