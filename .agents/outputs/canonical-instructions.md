# Canonical instruction layout

## Change

- Made `.agents/AGENTS.md` the canonical repository instruction file.
- Made `.agents/pi_AGENTS.md` the canonical Pi-specific instruction file.
- Made `.agents/ohmypiagents.md` the Oh My Pi-specific supplement.
- Retained root `AGENTS.md` and `pi_AGENTS.md` as explicit compatibility entrypoints that link to the canonical files.
- Updated project, architecture, roadmap, and decision memory with the layout and current schema-v7/test baseline.

## Validation

- `git diff --check`: clean.
- Canonical files and root shim links: present and verified.
- Full suite from `agentops/`: 357 passing, 4 environment skips.
- No product code or packaging files changed; executable rebuild not required.

## Follow-up

Root-only loaders must follow the compatibility links to receive the full canonical instructions. The shims intentionally remain small to avoid maintaining two independent instruction sets.
