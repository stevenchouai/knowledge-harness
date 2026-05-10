# Fake vault demo

This demo shows the public-safe path through `knowledge-harness`: create a tiny
fake vault, assemble a dry-run prompt, inspect the run metadata, and clean up. It
does not require Codex and does not read a real Obsidian vault.

## One-command demo

From the repo root:

```bash
python3 -m pip install -e .
knowledge-harness demo
```

The command creates a temporary fake vault and temporary run directory, assembles
the same dry-run prompt described below, and prints the evidence paths:

```text
knowledge-harness demo
fake_vault: /tmp/knowledge-harness-demo-.../fake-vault
run_dir: /tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS
prompt_file: /tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS/prompt.txt
metadata_file: /tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS/run.json
status: no real vault or Codex was used
cleanup: rm -rf /tmp/knowledge-harness-demo-...
```

Use the printed `prompt_file` and `metadata_file` paths to inspect the evidence.
The command does not read `config/harness.json`, does not edit it, and does not
invoke Codex.

For a copy/pasteable Markdown proof receipt, run:

```bash
knowledge-harness demo --markdown
```

Use Markdown when a human needs to paste the receipt into a README, GitHub
issue, audit note, or chat thread:

```markdown
# knowledge-harness demo receipt

command: `knowledge-harness demo --markdown`

## Paths

- fake_vault: `/tmp/knowledge-harness-demo-.../fake-vault`
- run_dir: `/tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS`
- prompt_file: `/tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS/prompt.txt`
- metadata_file: `/tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS/run.json`

## Safety claims

- `real_vault_used=false`
- `codex_used=false`
- `write_output=false`
- `dry_run=true`

## Evidence to check

- `prompt_file` contains the fake vault contracts and demo question.
- `metadata_file` records `dry_run=true` and `write_output=false`.
- `metadata_file` records a missing demo Codex path and no `--add-dir` vault grant.
- `run_dir` has no `last_message.txt`, which dry runs would only create if Codex ran.
```

For a machine-readable proof receipt, run:

```bash
knowledge-harness demo --json
```

Use JSON when another script, CI job, or agent needs to parse paths and safety
booleans. Expected output is JSON like:

```json
{
  "fake_vault": "/tmp/knowledge-harness-demo-.../fake-vault",
  "run_dir": "/tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS",
  "prompt_file": "/tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS/prompt.txt",
  "metadata_file": "/tmp/knowledge-harness-demo-.../runs/YYYYMMDD-HHMMSS/run.json",
  "status": "no real vault or Codex was used",
  "real_vault_used": false,
  "codex_used": false,
  "write_output": false,
  "dry_run": true,
  "cleanup_root": "/tmp/knowledge-harness-demo-...",
  "cleanup_command": "rm -rf /tmp/knowledge-harness-demo-..."
}
```

## Manual walkthrough

This older walkthrough is for people who want to see the config-based path by
hand. Unlike `knowledge-harness demo`, it temporarily rewrites
`config/harness.json` and restores it at the end. Use the one-command demo above
for the public-safe path that leaves local config untouched.

Estimated time: about 5 minutes.

### 1. Install the CLI

From the repo root:

```bash
python3 -m pip install -e .
```

### 2. Create a fake vault

```bash
DEMO_ROOT=/tmp/knowledge-harness-demo
FAKE_VAULT="$DEMO_ROOT/fake-vault"
DEMO_RUNS="$DEMO_ROOT/runs"
CONFIG_BACKUP="$DEMO_ROOT/harness.backup.json"

rm -rf "$DEMO_ROOT"
mkdir -p "$FAKE_VAULT/wiki"
```

Create the minimal vault contract files that the harness expects:

```bash
cat > "$FAKE_VAULT/AGENTS.md" <<'EOF'
# AGENTS.md

Use this fake vault only for public demos.
Do not assume private knowledge exists.
Answer from the provided index and prompt contract.
EOF

cat > "$FAKE_VAULT/PROMPTS.md" <<'EOF'
# PROMPTS.md

## demo-query

Route public demo questions through the fake wiki index.
Prefer read-only behavior unless a command explicitly allows writing.
EOF

cat > "$FAKE_VAULT/wiki/_index.md" <<'EOF'
# Fake Wiki Index

- [[public-demo]] explains the sanitized demo flow.
- [[read-only-routing]] records that dry runs assemble prompts without invoking Codex.
EOF
```

### 3. Point the harness at the fake vault

Back up the local config first, then write a demo config:

```bash
if [ -f config/harness.json ]; then
  cp config/harness.json "$CONFIG_BACKUP"
fi

cat > config/harness.json <<EOF
{
  "vault_path": "$FAKE_VAULT",
  "codex_path": "$DEMO_ROOT/missing-codex",
  "model": "demo-model",
  "run_dir": "$DEMO_RUNS"
}
EOF
```

The `codex_path` intentionally points at a missing file. The dry-run query below
will prepare a command but will not execute Codex.

### 4. Inspect the effective config

```bash
knowledge-harness config
```

You should see JSON with `vault_path` under `/tmp/knowledge-harness-demo` and
`run_dir` under `/tmp/knowledge-harness-demo/runs`.

### 5. Assemble a dry-run query

```bash
knowledge-harness query "How does this harness route a public-safe question?" --dry-run --language en
```

Expected output includes paths like:

```text
run_dir: /tmp/knowledge-harness-demo/runs/YYYYMMDD-HHMMSS
prompt_file: /tmp/knowledge-harness-demo/runs/YYYYMMDD-HHMMSS/prompt.txt
output_file: /tmp/knowledge-harness-demo/runs/YYYYMMDD-HHMMSS/last_message.txt
dry_run: command prepared but not executed
```

Because this is a dry run, `last_message.txt` is only the planned output path.
It is not created by the harness.

### 6. Inspect the generated run

Capture the newest run directory:

```bash
RUN_DIR="$(ls -td "$DEMO_RUNS"/* | head -n 1)"
```

Inspect the assembled prompt:

```bash
sed -n '1,120p' "$RUN_DIR/prompt.txt"
```

The prompt should include the fake `AGENTS.md`, `PROMPTS.md`, and
`wiki/_index.md` contents from `/tmp/knowledge-harness-demo/fake-vault`.

Inspect the run metadata:

```bash
cat "$RUN_DIR/run.json"
```

The metadata should include:

```json
{
  "write_output": false,
  "dry_run": true,
  "language": "en"
}
```

It also records the prepared Codex command. In this demo, that command is not
executed.

Confirm the fake vault was not changed:

```bash
find "$FAKE_VAULT" -type f | sort
```

The output should still list only:

```text
/tmp/knowledge-harness-demo/fake-vault/AGENTS.md
/tmp/knowledge-harness-demo/fake-vault/PROMPTS.md
/tmp/knowledge-harness-demo/fake-vault/wiki/_index.md
```

### 7. Clean up

Restore the previous local config if a backup exists:

```bash
if [ -f "$CONFIG_BACKUP" ]; then
  cp "$CONFIG_BACKUP" config/harness.json
else
  rm -f config/harness.json
fi
```

Remove the temporary fake vault and run artifacts:

```bash
rm -rf /tmp/knowledge-harness-demo
```

This manual walkthrough only writes temporary files under `/tmp` plus the local
`config/harness.json` edit that the restore step reverses.
