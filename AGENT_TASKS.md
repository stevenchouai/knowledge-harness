# AGENT_TASKS

Autonomous agents should keep changes scoped to this harness repo. Do not edit vault
content, `.venv`, or existing `runs` artifacts. Prefer temporary directories for
tests that need fake vaults or run output.

## 1. Make dry-run skip Codex binary validation

- Context: `query --dry-run` promises to build the prompt and command without
  executing Codex, but `run_query()` currently requires the Codex binary to exist.
- Target files: `src/knowledge_harness/cli.py`, `tests/test_cli.py`,
  `tests/__init__.py`
- Acceptance criteria: dry-run query validates the vault contract but does not
  fail when `codex_path` is missing; doctor and real query still require Codex.
- Test command: `python3 -m unittest discover`
- Risk: accidentally weakening validation for real executions.
- Rollback: revert the CLI validation split and remove the dry-run tests.

## 2. Add helpful config error messages

- Context: malformed `config/harness.json` currently surfaces raw JSON or key
  errors without explaining which local setting is wrong.
- Target files: `src/knowledge_harness/cli.py`, `tests/test_cli.py`
- Acceptance criteria: invalid JSON, missing required keys, and non-string values
  raise `SystemExit` with actionable messages naming the config path or field.
- Test command: `python3 -m unittest discover`
- Risk: changing existing exception text that a script might inspect.
- Rollback: restore the old `load_config()` implementation and remove tests.

## 3. Add a `config` inspection command

- Context: users need to confirm effective vault, Codex, model, and run paths
  without invoking doctor checks against local binaries.
- Target files: `src/knowledge_harness/cli.py`, `README.md`, `tests/test_cli.py`
- Acceptance criteria: `knowledge-harness config` prints the effective config as
  JSON and does not require the vault or Codex binary to exist.
- Test command: `python3 -m unittest discover`
- Risk: exposing paths in logs when users paste output publicly.
- Rollback: remove the subcommand, docs entry, and tests.

## 4. Add query output-name validation

- Context: `--output-name` is interpolated into a vault output path and should be
  constrained before being passed to Codex.
- Target files: `src/knowledge_harness/cli.py`, `tests/test_cli.py`
- Acceptance criteria: names with path separators, parent traversal, empty names,
  or non-`.md` suffixes are rejected before any run directory is created.
- Test command: `python3 -m unittest discover`
- Risk: rejecting a filename pattern the user currently relies on.
- Rollback: remove the validator and associated tests.

## 5. Add `--language` for query answers

- Context: query prompts always require Chinese answers, but some future runs may
  need English while preserving the same context assembly.
- Target files: `src/knowledge_harness/cli.py`, `README.md`, `tests/test_cli.py`
- Acceptance criteria: `--language zh|en` defaults to `zh`; generated prompt uses
  the selected language instruction; invalid values are rejected by argparse.
- Test command: `python3 -m unittest discover`
- Risk: changing prompt wording may affect answer style.
- Rollback: remove the argument and restore the fixed Chinese instruction.

## 6. Add prompt snapshot preview tests

- Context: the prompt builder embeds vault files and instructions, but there is no
  guard against accidental removal of the vault contract sections.
- Target files: `tests/test_cli.py`
- Acceptance criteria: tests assert the prompt contains AGENTS, PROMPTS, index,
  output rules, the user question, and local path citations.
- Test command: `python3 -m unittest discover`
- Risk: brittle tests if prompt wording intentionally evolves.
- Rollback: delete or relax the prompt snapshot tests.

## 7. Add run metadata versioning

- Context: `run.json` has no schema version, making future readers guess which
  fields exist.
- Target files: `src/knowledge_harness/cli.py`, `docs/ARCHITECTURE.md`,
  `tests/test_cli.py`
- Acceptance criteria: new run metadata includes `schema_version: 1` and tests
  assert the field is present for dry-run metadata.
- Test command: `python3 -m unittest discover`
- Risk: downstream scripts may not expect the extra key.
- Rollback: remove the metadata field and docs note.

## 8. Add safe run-directory creation helper

- Context: `run_query()` directly creates timestamped directories, which makes
  collision behavior implicit.
- Target files: `src/knowledge_harness/cli.py`, `tests/test_cli.py`
- Acceptance criteria: a helper creates a unique run directory, handles timestamp
  collision deterministically, and is covered with tests.
- Test command: `python3 -m unittest discover`
- Risk: changing run directory names could surprise manual workflows.
- Rollback: restore direct `make_run_stamp()` directory creation.

## 9. Add `doctor --json`

- Context: doctor output is human-readable only, which limits automated checks.
- Target files: `src/knowledge_harness/cli.py`, `README.md`, `tests/test_cli.py`
- Acceptance criteria: `doctor --json` emits machine-readable status for vault,
  Codex, model, and run directory while preserving current text output by default.
- Test command: `python3 -m unittest discover`
- Risk: users may confuse status reporting with automatic repair.
- Rollback: remove the JSON mode and tests.

## 10. Improve missing vault file reporting

- Context: missing required vault files are reported as a Python list string.
- Target files: `src/knowledge_harness/cli.py`, `tests/test_cli.py`
- Acceptance criteria: the error lists each missing file on its own line and names
  the expected vault root.
- Test command: `python3 -m unittest discover`
- Risk: scripts relying on the current single-line error may need adjustment.
- Rollback: restore the old missing-file message.

## 11. Add subprocess failure metadata

- Context: real query runs return Codex's exit code but do not record whether the
  subprocess succeeded or failed in metadata.
- Target files: `src/knowledge_harness/cli.py`, `tests/test_cli.py`
- Acceptance criteria: after execution, `run.json` includes `exit_code` and an
  `ended_at` timestamp without overwriting existing fields.
- Test command: `python3 -m unittest discover`
- Risk: interrupted runs may leave partial metadata.
- Rollback: remove the metadata update block and tests.

## 12. Add an explicit prompt-only command

- Context: agents may need to inspect the assembled prompt without creating a run
  directory or storing metadata.
- Target files: `src/knowledge_harness/cli.py`, `README.md`, `tests/test_cli.py`
- Acceptance criteria: `knowledge-harness prompt "question"` prints the prompt to
  stdout, validates only vault files, and does not touch `runs`.
- Test command: `python3 -m unittest discover`
- Risk: prompt content may include sensitive vault excerpts in terminal history.
- Rollback: remove the subcommand, README example, and tests.

## 13. Add README command examples for dry-run

- Context: README shows doctor and query examples but not the safer dry-run path
  for checking prompt assembly.
- Target files: `README.md`
- Acceptance criteria: README includes one `query --dry-run` example and explains
  where prompt and metadata files are written.
- Test command: `python3 -m compileall src`
- Risk: docs may imply dry-run is side-effect free even though it writes a run
  snapshot.
- Rollback: remove the README dry-run section.

## 14. Add architecture note for shadow review data

- Context: the architecture mentions shadow evaluation but not a concrete metadata
  shape for later review.
- Target files: `docs/ARCHITECTURE.md`
- Acceptance criteria: architecture docs list proposed fields for prompt snapshot,
  files read, files written, final answer, and lightweight quality notes.
- Test command: `python3 -m compileall src`
- Risk: documenting fields before implementation may create false expectations.
- Rollback: remove the added architecture subsection.

## 15. Add type-friendly parser construction tests

- Context: parser behavior is central to the CLI, but no tests ensure subcommands
  and options parse as expected.
- Target files: `tests/test_cli.py`
- Acceptance criteria: tests cover `doctor`, query basics, `--write-output`,
  `--output-name`, and `--dry-run` parsing.
- Test command: `python3 -m unittest discover`
- Risk: tests may need updates whenever CLI options change.
- Rollback: delete parser tests.

## 16. Add model override option

- Context: the model comes only from config, making one-off local experiments more
  cumbersome than necessary.
- Target files: `src/knowledge_harness/cli.py`, `README.md`, `tests/test_cli.py`
- Acceptance criteria: `query --model MODEL` overrides config for that run and is
  recorded in `run.json`; default behavior remains unchanged.
- Test command: `python3 -m unittest discover`
- Risk: users may run expensive models accidentally.
- Rollback: remove the option and restore config-only model selection.

## 17. Add command redaction for metadata

- Context: `run.json` stores the full command, including absolute local paths.
- Target files: `src/knowledge_harness/cli.py`, `tests/test_cli.py`
- Acceptance criteria: metadata keeps the executable and flags needed for audit
  while optionally redacting configured sensitive roots.
- Test command: `python3 -m unittest discover`
- Risk: over-redaction may make debugging harder.
- Rollback: restore full command serialization.

## 18. Add proposal directory convention

- Context: architecture recommends `proposals/`, but the repo has no placeholder
  or naming convention.
- Target files: `docs/ARCHITECTURE.md`, `proposals/README.md`
- Acceptance criteria: docs define proposal filename format and required sections:
  observed pattern, suspected issue, suggested change, upside, risk.
- Test command: `python3 -m compileall src`
- Risk: adding process docs before tooling may add maintenance burden.
- Rollback: remove `proposals/README.md` and the architecture addition.

## 19. Add path expansion tests for config

- Context: config paths use `expanduser()`, but this behavior is untested.
- Target files: `tests/test_cli.py`
- Acceptance criteria: tests verify `~` expands for vault, Codex, and run paths
  while preserving model text.
- Test command: `python3 -m unittest discover`
- Risk: tests may depend on the running user's home directory.
- Rollback: remove path expansion tests.

## 20. Add a minimal release checklist

- Context: the repo has no checklist for safe changes to a harness that can touch
  an external vault.
- Target files: `docs/RELEASE_CHECKLIST.md`
- Acceptance criteria: checklist includes scope review, vault write check, run
  artifact check, tests, and manual dry-run review.
- Test command: `python3 -m compileall src`
- Risk: checklist can go stale if release process changes.
- Rollback: delete `docs/RELEASE_CHECKLIST.md`.
