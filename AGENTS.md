# AGENTS.md

Guidance for agents working in this repo.

## Mission

`knowledge-harness` is the runtime layer around Steven Chou's Obsidian knowledge vault. The vault is the source of truth for knowledge; this repo should contain execution code, config shape, tests, docs, and run/proposal conventions — not copied private vault content.

## Repo map

- `src/knowledge_harness/` — CLI implementation.
- `tests/` — unit tests and sanitized fixtures.
- `config/harness.json` — local wiring for vault path and Codex binary.
- `docs/` — architecture and operating notes.
- `runs/` — generated prompt/run snapshots; treat existing artifacts as local state.
- `AGENT_TASKS.md` — backlog of safe autonomous tasks with acceptance criteria.

## Working rules

- Keep changes scoped to this harness repo unless the user explicitly asks for vault edits.
- Do not edit Steven's Obsidian vault content from this repo work. If a command needs a vault, use temporary fake vault fixtures in tests.
- Do not commit private vault excerpts, run outputs with personal content, absolute private paths in public docs, API keys, tokens, or Codex credentials.
- Prefer read-only health checks and explicit `--write-output` style gates for anything that can mutate the vault.
- Implement tasks from `AGENT_TASKS.md` with tests and the listed rollback path.
- Use repo-relative links in documentation.

## Commands

```bash
python3 -m pip install -e .
knowledge-harness doctor
knowledge-harness vault-health
knowledge-harness query "..." --dry-run
python3 -m unittest discover
python3 -m compileall src
```

## Validation

- CLI behavior change: run `python3 -m unittest discover`.
- Docs-only change: run `python3 -m compileall src` if Python files are untouched, and manually verify changed links.
- Vault-write-related change: add/adjust tests that prove dry-run/read-only behavior stays read-only and explicit write behavior remains gated.

## Handoff checklist

- Summarize changed files with repo-relative paths.
- State validation commands and results.
- If any command touched `runs/`, explain whether it is generated local state and whether it was committed.
