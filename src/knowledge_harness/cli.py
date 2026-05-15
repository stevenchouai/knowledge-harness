from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
import tempfile
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from knowledge_harness.vault_health import VaultHealthReport, scan_vault_health


DEFAULT_VAULT = Path("~/Obsidian/KnowledgeVault")
DEFAULT_CODEX = Path("/Applications/Codex.app/Contents/Resources/codex")
DEFAULT_MODEL = "gpt-5.4"
RUN_METADATA_SCHEMA_VERSION = 1
LANGUAGE_CHOICES = ("zh", "en")
DEMO_QUESTION = "How does this harness route a public-safe question?"
DEMO_AGENTS = """# AGENTS.md

Use this fake vault only for public demos.
Do not assume private knowledge exists.
Answer from the provided index and prompt contract.
"""
DEMO_PROMPTS = """# PROMPTS.md

## demo-query

Route public demo questions through the fake wiki index.
Prefer read-only behavior unless a command explicitly allows writing.
"""
DEMO_INDEX = """# Fake Wiki Index

- [[public-demo]] explains the sanitized demo flow.
- [[read-only-routing]] records that dry runs assemble prompts without invoking Codex.
"""
LANGUAGE_PROMPT_INSTRUCTIONS = {
    "zh": {
        "answer": "Answer in Chinese.",
        "structure": "Structure: 先结论 -> 再依据 -> 再下一步建议.",
    },
    "en": {
        "answer": "Answer in English.",
        "structure": "Structure: conclusion first -> evidence next -> suggested next steps.",
    },
}
T = TypeVar("T")


@dataclass
class HarnessConfig:
    vault_path: Path
    codex_path: Path
    model: str
    run_dir: Path


def load_config(repo_root: Path) -> HarnessConfig:
    config_path = repo_root / "config" / "harness.json"
    required_fields = {"vault_path", "codex_path", "model", "run_dir"}
    payload = {
        "vault_path": str(DEFAULT_VAULT),
        "codex_path": str(DEFAULT_CODEX),
        "model": DEFAULT_MODEL,
        "run_dir": str(repo_root / "runs"),
    }

    if config_path.exists():
        try:
            config_text = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"Could not read config file {config_path}: {exc}") from exc

        try:
            loaded = json.loads(config_text)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"Invalid JSON in config file {config_path}: {exc.msg} "
                f"(line {exc.lineno}, column {exc.colno})"
            ) from exc

        if not isinstance(loaded, dict):
            raise SystemExit(f"Config file {config_path} must contain a JSON object")

        missing = sorted(required_fields - loaded.keys())
        if missing:
            raise SystemExit(
                f"Config file {config_path} is missing required field(s): "
                f"{', '.join(missing)}"
            )
        payload.update(loaded)

    for field in sorted(required_fields):
        if not isinstance(payload.get(field), str):
            raise SystemExit(f"Config field {field!r} in {config_path} must be a string")

    return HarnessConfig(
        vault_path=Path(payload["vault_path"]).expanduser(),
        codex_path=Path(payload["codex_path"]).expanduser(),
        model=payload["model"],
        run_dir=Path(payload["run_dir"]).expanduser(),
    )


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def ensure_vault_contract(config: HarnessConfig) -> None:
    required = [
        config.vault_path / "AGENTS.md",
        config.vault_path / "PROMPTS.md",
        config.vault_path / "wiki" / "_index.md",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        relative_missing = [path.relative_to(config.vault_path) for path in missing]
        missing_lines = "\n".join(f"- {path.as_posix()}" for path in relative_missing)
        raise SystemExit(
            "Missing required knowledge-base files under "
            f"{config.vault_path}:\n{missing_lines}"
        )


def ensure_codex_binary(config: HarnessConfig) -> None:
    if not config.codex_path.exists():
        raise SystemExit(f"Codex binary not found: {config.codex_path}")


def ensure_workspace(config: HarnessConfig) -> None:
    ensure_vault_contract(config)
    ensure_codex_binary(config)


def build_query_prompt(
    config: HarnessConfig,
    question: str,
    output_name: str | None,
    write_output: bool,
    language: str = "zh",
) -> str:
    language_instructions = LANGUAGE_PROMPT_INSTRUCTIONS[language]
    agents = read_file(config.vault_path / "AGENTS.md")
    prompts = read_file(config.vault_path / "PROMPTS.md")
    index_text = read_file(config.vault_path / "wiki" / "_index.md")
    output_clause = "Do not write files unless needed for the answer."
    if write_output and output_name:
        output_clause = (
            "Write the final answer to "
            f"`{config.vault_path / 'wiki' / 'outputs' / output_name}` and "
            f"append a query entry to `{config.vault_path / 'wiki' / '_log.md'}`."
        )

    return textwrap.dedent(
        f"""
        You are Steven Chou's digital twin operating through an external harness repo.

        Working rules:
        - Treat `{config.vault_path}` as the knowledge base root.
        - First read `AGENTS.md` and `wiki/_index.md` from that vault before answering.
        - Follow the knowledge-query flow from the vault schema.
        - {language_instructions["answer"]}
        - {language_instructions["structure"]}
        - Cite relevant local file paths in the answer.
        - {output_clause}

        User question:
        {question}

        Vault context snapshot:
        <AGENTS.md>
        {agents}
        </AGENTS.md>

        <PROMPTS.md>
        {prompts}
        </PROMPTS.md>

        <wiki/_index.md>
        {index_text}
        </wiki/_index.md>
        """
    ).strip()


def make_run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_run_dir(run_root: Path) -> Path:
    stamp = make_run_stamp()
    for suffix in range(0, sys.maxsize):
        run_name = stamp if suffix == 0 else f"{stamp}-{suffix}"
        run_path = run_root / run_name
        try:
            run_path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return run_path

    raise SystemExit(f"Could not create a unique run directory under {run_root}")


def build_doctor_status(config: HarnessConfig) -> dict[str, object]:
    vault_files = [
        config.vault_path / "AGENTS.md",
        config.vault_path / "PROMPTS.md",
        config.vault_path / "wiki" / "_index.md",
    ]
    vault_ok = all(path.exists() for path in vault_files)
    codex_ok = config.codex_path.exists()
    model_ok = bool(config.model.strip())
    run_dir_ok = config.run_dir.exists() or config.run_dir.parent.exists()
    return {
        "ok": vault_ok and codex_ok and model_ok and run_dir_ok,
        "vault_path": str(config.vault_path),
        "vault_ok": vault_ok,
        "missing_vault_files": [str(path) for path in vault_files if not path.exists()],
        "codex_path": str(config.codex_path),
        "codex_ok": codex_ok,
        "model": config.model,
        "model_ok": model_ok,
        "run_dir": str(config.run_dir),
        "run_dir_ok": run_dir_ok,
    }


def run_doctor(config: HarnessConfig, *, json_output: bool = False) -> int:
    if json_output:
        status = build_doctor_status(config)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["ok"] else 1

    ensure_workspace(config)
    print("knowledge-harness doctor")
    print(f"vault_path: {config.vault_path}")
    print(f"codex_path: {config.codex_path}")
    print(f"model: {config.model}")
    print(f"run_dir: {config.run_dir}")
    return 0


def run_config(config: HarnessConfig) -> int:
    print(
        json.dumps(
            {
                "vault_path": str(config.vault_path),
                "codex_path": str(config.codex_path),
                "model": config.model,
                "run_dir": str(config.run_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def write_demo_fake_vault(vault_path: Path) -> None:
    wiki_path = vault_path / "wiki"
    wiki_path.mkdir(parents=True, exist_ok=True)
    (vault_path / "AGENTS.md").write_text(DEMO_AGENTS, encoding="utf-8")
    (vault_path / "PROMPTS.md").write_text(DEMO_PROMPTS, encoding="utf-8")
    (wiki_path / "_index.md").write_text(DEMO_INDEX, encoding="utf-8")


def latest_run_path(run_root: Path) -> Path:
    run_paths = sorted(path for path in run_root.iterdir() if path.is_dir())
    if not run_paths:
        raise SystemExit(f"Demo did not create a run directory under {run_root}")
    return run_paths[-1]


def verify_demo_evidence(demo_root: Path, fake_vault: Path, run_path: Path) -> None:
    prompt_path = run_path / "prompt.txt"
    metadata_path = run_path / "run.json"
    output_path = run_path / "last_message.txt"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    command = metadata.get("command", [])
    if not metadata.get("dry_run"):
        raise SystemExit(f"Demo metadata did not record dry_run=true: {metadata_path}")
    if metadata.get("write_output"):
        raise SystemExit(f"Demo metadata unexpectedly allowed vault writes: {metadata_path}")
    if command[:1] != ["<codex_path>"]:
        raise SystemExit(
            f"Demo metadata did not redact the missing demo Codex path: {metadata_path}"
        )
    command_text = json.dumps(command, ensure_ascii=False)
    if "--add-dir" in command or str(fake_vault) in command_text:
        raise SystemExit(f"Demo metadata unexpectedly granted vault access: {metadata_path}")
    if str(demo_root) in command_text:
        raise SystemExit(f"Demo metadata exposed the demo root path: {metadata_path}")
    if output_path.exists():
        raise SystemExit(f"Demo dry run unexpectedly created an output file: {output_path}")

    prompt = prompt_path.read_text(encoding="utf-8")
    for snippet in (DEMO_AGENTS.strip(), DEMO_PROMPTS.strip(), DEMO_INDEX.strip()):
        if snippet not in prompt:
            raise SystemExit(f"Demo prompt is missing fake vault content: {prompt_path}")


def build_demo_receipt(demo_root: Path, fake_vault: Path, run_path: Path) -> dict[str, object]:
    return {
        "fake_vault": str(fake_vault),
        "run_dir": str(run_path),
        "prompt_file": str(run_path / "prompt.txt"),
        "metadata_file": str(run_path / "run.json"),
        "status": "no real vault or Codex was used",
        "real_vault_used": False,
        "codex_used": False,
        "write_output": False,
        "dry_run": True,
        "cleanup_root": str(demo_root),
        "cleanup_command": f"rm -rf {shlex.quote(str(demo_root))}",
    }


def format_demo_markdown_receipt(receipt: dict[str, object]) -> str:
    safety_claims = (
        ("real_vault_used", receipt["real_vault_used"]),
        ("codex_used", receipt["codex_used"]),
        ("write_output", receipt["write_output"]),
        ("dry_run", receipt["dry_run"]),
    )
    safety_lines = [
        f"- `{key}={str(value).lower()}`" for key, value in safety_claims
    ]
    lines = [
        "# knowledge-harness demo receipt",
        "",
        "command: `knowledge-harness demo --markdown`",
        "",
        "## Paths",
        "",
        f"- fake_vault: `{receipt['fake_vault']}`",
        f"- run_dir: `{receipt['run_dir']}`",
        f"- prompt_file: `{receipt['prompt_file']}`",
        f"- metadata_file: `{receipt['metadata_file']}`",
        "",
        "## Safety claims",
        "",
        *safety_lines,
        "",
        "## Evidence to check",
        "",
        "- `prompt_file` contains the fake vault contracts and demo question.",
        "- `metadata_file` records `dry_run=true` and `write_output=false`.",
        "- `metadata_file` records a missing demo Codex path and no `--add-dir` vault grant.",
        "- `run_dir` has no `last_message.txt`, which dry runs would only create if Codex ran.",
        "",
        "## Cleanup",
        "",
        f"cleanup_command: `{receipt['cleanup_command']}`",
    ]
    return "\n".join(lines)


def html_escape(value: object) -> str:
    escaped = html.escape(str(value), quote=True)
    for pattern in (r"javascript:", r"https://", r"http://"):
        escaped = re.sub(
            pattern,
            lambda match: match.group(0).replace(":", "&#58;", 1),
            escaped,
            flags=re.IGNORECASE,
        )
    for pattern in (r"onload=", r"onclick="):
        escaped = re.sub(
            pattern,
            lambda match: match.group(0).replace("=", "&#61;", 1),
            escaped,
            flags=re.IGNORECASE,
        )
    return escaped


def format_demo_html_receipt(receipt: dict[str, object]) -> str:
    safety_claims = (
        ("real_vault_used", receipt["real_vault_used"]),
        ("codex_used", receipt["codex_used"]),
        ("write_output", receipt["write_output"]),
        ("dry_run", receipt["dry_run"]),
    )
    safety_items = "\n".join(
        "          "
        f"<li><code>{html_escape(key)}={html_escape(str(value).lower())}</code></li>"
        for key, value in safety_claims
    )
    paths = (
        ("Fake vault path", receipt["fake_vault"]),
        ("Run directory", receipt["run_dir"]),
        ("Prompt file", receipt["prompt_file"]),
        ("Metadata file", receipt["metadata_file"]),
    )
    path_rows = "\n".join(
        "              "
        f"<tr><th scope=\"row\">{html_escape(label)}</th>"
        f"<td><code>{html_escape(value)}</code></td></tr>"
        for label, value in paths
    )
    evidence_items = (
        "`prompt_file` contains the fake vault contracts and demo question.",
        "`metadata_file` records `dry_run=true` and `write_output=false`.",
        "`metadata_file` records a missing demo Codex path and no `--add-dir` vault grant.",
        "`run_dir` has no `last_message.txt`, which dry runs would only create if Codex ran.",
    )
    evidence_lines = "\n".join(
        f"          <li>{html_escape(item)}</li>" for item in evidence_items
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>knowledge-harness demo receipt</title>
    <style>
      :root {{
        color-scheme: light;
        --ink: #17211b;
        --muted: #5a665f;
        --paper: #fbfaf5;
        --line: #d9d1bf;
        --accent: #0f6b57;
        --accent-soft: #e1f0e8;
        --panel: #ffffff;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        background:
          linear-gradient(135deg, rgba(15, 107, 87, 0.08), rgba(206, 86, 58, 0.07)),
          var(--paper);
        color: var(--ink);
        font-family: Charter, "Bitstream Charter", Cambria, Georgia, serif;
        line-height: 1.5;
      }}
      main {{
        width: min(960px, calc(100% - 32px));
        margin: 0 auto;
        padding: 48px 0;
      }}
      .receipt {{
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel);
        box-shadow: 0 18px 60px rgba(23, 33, 27, 0.12);
        overflow: hidden;
      }}
      .header {{
        padding: 28px;
        border-bottom: 1px solid var(--line);
        background: #f4efe4;
      }}
      .eyebrow {{
        margin: 0 0 8px;
        color: var(--accent);
        font: 700 12px/1.2 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        letter-spacing: 0;
        text-transform: uppercase;
      }}
      h1 {{
        margin: 0;
        font-size: clamp(30px, 4vw, 52px);
        line-height: 1;
        letter-spacing: 0;
      }}
      .status {{
        margin: 16px 0 0;
        color: var(--muted);
        max-width: 64ch;
      }}
      section {{
        padding: 24px 28px;
        border-bottom: 1px solid var(--line);
      }}
      section:last-child {{
        border-bottom: 0;
      }}
      h2 {{
        margin: 0 0 14px;
        font-size: 18px;
        letter-spacing: 0;
      }}
      code {{
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 0.94em;
        word-break: break-word;
      }}
      .command {{
        display: block;
        padding: 12px 14px;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: #18221c;
        color: #f8f4e9;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
      }}
      th, td {{
        padding: 12px 0;
        border-top: 1px solid var(--line);
        text-align: left;
        vertical-align: top;
      }}
      th {{
        width: 180px;
        padding-right: 16px;
        color: var(--muted);
        font-weight: 700;
      }}
      ul {{
        margin: 0;
        padding-left: 20px;
      }}
      li + li {{
        margin-top: 8px;
      }}
      .claims {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 10px;
        padding: 0;
        list-style: none;
      }}
      .claims li {{
        margin: 0;
        padding: 10px 12px;
        border: 1px solid #b8dccb;
        border-radius: 6px;
        background: var(--accent-soft);
      }}
      @media (max-width: 640px) {{
        main {{
          width: min(100% - 20px, 960px);
          padding: 20px 0;
        }}
        .header,
        section {{
          padding: 20px;
        }}
        th, td {{
          display: block;
          width: 100%;
          padding: 8px 0;
        }}
        td {{
          border-top: 0;
          padding-bottom: 14px;
        }}
      }}
    </style>
  </head>
  <body>
    <main>
      <article class="receipt" aria-labelledby="receipt-title">
        <header class="header">
          <p class="eyebrow">Public-safe proof</p>
          <h1 id="receipt-title">knowledge-harness demo receipt</h1>
          <p class="status">{html_escape(receipt["status"])}</p>
        </header>

        <section aria-labelledby="command-title">
          <h2 id="command-title">Command</h2>
          <code class="command">knowledge-harness demo --html</code>
        </section>

        <section aria-labelledby="paths-title">
          <h2 id="paths-title">Evidence Paths</h2>
          <table>
            <tbody>
{path_rows}
            </tbody>
          </table>
        </section>

        <section aria-labelledby="claims-title">
          <h2 id="claims-title">Safety Claims</h2>
          <ul class="claims">
{safety_items}
          </ul>
        </section>

        <section aria-labelledby="checklist-title">
          <h2 id="checklist-title">Evidence Checklist</h2>
          <ul>
{evidence_lines}
          </ul>
        </section>

        <section aria-labelledby="cleanup-title">
          <h2 id="cleanup-title">Cleanup</h2>
          <code>{html_escape(receipt["cleanup_command"])}</code>
        </section>
      </article>
    </main>
  </body>
</html>"""


def run_demo(
    repo_root: Path,
    demo_root: Path | None = None,
    *,
    json_output: bool = False,
    markdown_output: bool = False,
    html_output: bool = False,
    save_html: Path | None = None,
) -> int:
    if demo_root is None:
        demo_root = Path(tempfile.mkdtemp(prefix="knowledge-harness-demo-"))
    else:
        demo_root.mkdir(parents=True, exist_ok=True)

    fake_vault = demo_root / "fake-vault"
    run_root = demo_root / "runs"
    write_demo_fake_vault(fake_vault)

    config = HarnessConfig(
        vault_path=fake_vault,
        codex_path=demo_root / "missing-codex",
        model="demo-model",
        run_dir=run_root,
    )
    with redirect_stdout(io.StringIO()):
        result = run_query(
            repo_root=repo_root,
            config=config,
            question=DEMO_QUESTION,
            output_name=None,
            write_output=False,
            dry_run=True,
            language="en",
        )
    if result != 0:
        return result

    run_path = latest_run_path(run_root)
    verify_demo_evidence(demo_root, fake_vault, run_path)
    receipt = build_demo_receipt(demo_root, fake_vault, run_path)
    if json_output:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    if markdown_output:
        print(format_demo_markdown_receipt(receipt))
        return 0
    if html_output:
        print(format_demo_html_receipt(receipt))
        return 0
    if save_html is not None:
        save_html = save_html.expanduser()
        save_html.parent.mkdir(parents=True, exist_ok=True)
        save_html.write_text(format_demo_html_receipt(receipt), encoding="utf-8")
        print("knowledge-harness demo")
        print(f"saved_html: {save_html}")
        print(f"status: {receipt['status']}")
        print(f"cleanup: {receipt['cleanup_command']}")
        return 0

    print("knowledge-harness demo")
    print(f"fake_vault: {receipt['fake_vault']}")
    print(f"run_dir: {receipt['run_dir']}")
    print(f"prompt_file: {receipt['prompt_file']}")
    print(f"metadata_file: {receipt['metadata_file']}")
    print(f"status: {receipt['status']}")
    print(f"cleanup: {receipt['cleanup_command']}")
    return 0


def run_prompt(
    config: HarnessConfig,
    question: str,
    language: str = "zh",
) -> int:
    ensure_vault_contract(config)
    print(build_query_prompt(config, question, None, False, language))
    return 0


def run_vault_health(
    config: HarnessConfig,
    *,
    stale_days: int,
    review_limit: int,
    max_items: int,
    json_output: bool,
) -> int:
    report = scan_vault_health(
        config.vault_path,
        stale_days=stale_days,
        high_potential_limit=review_limit,
    )
    if json_output:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_vault_health_report(report, max_items=max_items))
    return 0


def format_vault_health_report(report: VaultHealthReport, *, max_items: int) -> str:
    max_items = max(1, max_items)
    lines = [
        "knowledge-harness vault-health",
        f"vault_path: {report.vault_path}",
        "mode: read-only",
        f"notes: {report.note_count}",
        f"stale_days: {report.stale_days}",
        "findings:",
        f"  orphan_notes: {len(report.orphan_notes)}",
        f"  broken_wikilinks: {len(report.broken_wikilinks)}",
        f"  empty_notes: {len(report.empty_notes)}",
        f"  duplicate_titles: {len(report.duplicate_titles)}",
        f"  stale_notes: {len(report.stale_notes)}",
        f"  high_potential_notes: {len(report.high_potential_notes)}",
    ]

    add_report_section(
        lines,
        "orphan_notes",
        report.orphan_notes,
        lambda path: path,
        max_items,
    )
    add_report_section(
        lines,
        "broken_wikilinks",
        report.broken_wikilinks,
        lambda link: f"{link.source_path}:{link.line} {link.raw} -> {link.target}",
        max_items,
    )
    add_report_section(
        lines,
        "empty_notes",
        report.empty_notes,
        lambda path: path,
        max_items,
    )
    add_report_section(
        lines,
        "duplicate_titles",
        report.duplicate_titles,
        lambda duplicate: f"{duplicate.title}: {', '.join(duplicate.paths)}",
        max_items,
    )
    add_report_section(
        lines,
        "stale_notes",
        report.stale_notes,
        lambda note: f"{note.path} ({note.days_since_modified} days)",
        max_items,
    )
    add_report_section(
        lines,
        "high_potential_notes",
        report.high_potential_notes,
        lambda note: (
            f"{note.path} (score {note.score}; in {note.inbound_links}; "
            f"out {note.outbound_links}; words {note.word_count}; "
            f"stale {note.days_since_modified} days)"
        ),
        max_items,
    )
    return "\n".join(lines)


def add_report_section(
    lines: list[str],
    title: str,
    items: Sequence[T],
    render_item: Callable[[T], str],
    max_items: int,
) -> None:
    if not items:
        return

    lines.append("")
    lines.append(f"{title}:")
    for item in items[:max_items]:
        lines.append(f"- {render_item(item)}")

    remaining = len(items) - max_items
    if remaining > 0:
        lines.append(f"- ... {remaining} more")


def validate_output_name(output_name: str | None) -> str | None:
    if output_name is None:
        return None

    if not output_name:
        raise SystemExit("output_name must not be empty")
    if output_name in {".", ".."} or ".." in Path(output_name).parts:
        raise SystemExit("output_name must not contain parent traversal")
    if Path(output_name).is_absolute():
        raise SystemExit("output_name must be a filename, not an absolute path")
    if "/" in output_name or "\\" in output_name:
        raise SystemExit("output_name must be a filename, not a path")
    if Path(output_name).suffix != ".md":
        raise SystemExit("output_name must end with .md")

    return output_name


def redact_command_for_metadata(
    command: Sequence[str],
    repo_root: Path,
    config: HarnessConfig,
) -> tuple[list[str], dict[str, str]]:
    descriptions = {
        "<codex_path>": "configured codex_path",
        "<repo_root>": "harness repository root",
        "<run_dir>": "configured run_dir",
        "<vault_path>": "configured vault_path",
    }
    rules = [
        (str(config.codex_path), "<codex_path>", False),
        (str(repo_root), "<repo_root>", True),
        (str(config.run_dir), "<run_dir>", True),
        (str(config.vault_path), "<vault_path>", True),
    ]
    rules.sort(key=lambda rule: len(rule[0]), reverse=True)

    used_placeholders: set[str] = set()
    redacted_command: list[str] = []
    for value in command:
        redacted = value
        for root_text, placeholder, include_descendants in rules:
            redacted = redact_path_text(value, root_text, placeholder, include_descendants)
            if redacted != value:
                used_placeholders.add(placeholder)
                break
        redacted_command.append(redacted)

    redactions = {
        placeholder: descriptions[placeholder]
        for placeholder in descriptions
        if placeholder in used_placeholders
    }
    return redacted_command, redactions


def redact_path_text(
    value: str,
    root_text: str,
    placeholder: str,
    include_descendants: bool,
) -> str:
    root_text = root_text.rstrip(os.sep) or os.sep
    if value == root_text:
        return placeholder
    if not include_descendants:
        return value

    prefix = root_text if root_text == os.sep else f"{root_text}{os.sep}"
    if not value.startswith(prefix):
        return value

    relative = value[len(prefix) :]
    return f"{placeholder}/{Path(relative).as_posix()}"


def build_codex_command(
    repo_root: Path,
    config: HarnessConfig,
    output_path: Path,
    *,
    write_output: bool,
) -> list[str]:
    cmd = [
        str(config.codex_path),
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(repo_root),
        "--model",
        config.model,
        "--output-last-message",
        str(output_path),
        "-",
    ]
    if write_output:
        cmd[5:5] = ["--add-dir", str(config.vault_path)]
    return cmd


def run_query(
    repo_root: Path,
    config: HarnessConfig,
    question: str,
    output_name: str | None,
    write_output: bool,
    dry_run: bool,
    language: str = "zh",
    model: str | None = None,
) -> int:
    if model is not None:
        config = HarnessConfig(
            vault_path=config.vault_path,
            codex_path=config.codex_path,
            model=model,
            run_dir=config.run_dir,
        )
    output_name = validate_output_name(output_name)
    if dry_run:
        ensure_vault_contract(config)
    else:
        ensure_workspace(config)
    run_path = create_run_dir(config.run_dir)

    prompt = build_query_prompt(config, question, output_name, write_output, language)
    prompt_path = run_path / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    output_path = run_path / "last_message.txt"
    cmd = build_codex_command(
        repo_root,
        config,
        output_path,
        write_output=write_output,
    )
    redacted_cmd, command_redactions = redact_command_for_metadata(cmd, repo_root, config)

    metadata = {
        "schema_version": RUN_METADATA_SCHEMA_VERSION,
        "question": question,
        "output_name": output_name,
        "write_output": write_output,
        "dry_run": dry_run,
        "language": language,
        "model": config.model,
        "command": redacted_cmd,
        "command_redactions": command_redactions,
    }
    (run_path / "run.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"run_dir: {run_path}")
    print(f"prompt_file: {prompt_path}")
    print(f"output_file: {output_path}")

    if dry_run:
        print("dry_run: command prepared but not executed")
        return 0

    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        cwd=repo_root,
        env=os.environ.copy(),
    )

    metadata.update(
        {
            "exit_code": proc.returncode,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    (run_path / "run.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return proc.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Codex through Steven's knowledge harness.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Validate local wiring.")
    doctor.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print machine-readable status instead of human-readable output.",
    )
    doctor.set_defaults(handler="doctor")

    config = subparsers.add_parser(
        "config",
        help="Print the effective harness configuration without validating local paths.",
    )
    config.set_defaults(handler="config")

    demo = subparsers.add_parser(
        "demo",
        help="Run a public-safe fake vault dry run without Codex or local config edits.",
    )
    demo_output = demo.add_mutually_exclusive_group()
    demo_output.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a machine-readable proof receipt instead of human-readable output.",
    )
    demo_output.add_argument(
        "--markdown",
        action="store_true",
        dest="markdown_output",
        help="Print a copy/pasteable Markdown proof receipt instead of human-readable output.",
    )
    demo_output.add_argument(
        "--html",
        action="store_true",
        dest="html_output",
        help="Print a self-contained HTML proof receipt instead of human-readable output.",
    )
    demo_output.add_argument(
        "--save-html",
        type=Path,
        dest="save_html",
        metavar="PATH",
        help="Write a self-contained HTML proof receipt to PATH.",
    )
    demo.set_defaults(handler="demo")

    prompt = subparsers.add_parser(
        "prompt",
        help="Print the assembled query prompt without creating a run or calling Codex.",
    )
    prompt.add_argument("question", help="The question to ask the digital twin.")
    prompt.add_argument(
        "--language",
        choices=LANGUAGE_CHOICES,
        default="zh",
        help="Answer language for the assembled prompt. Default: zh.",
    )
    prompt.set_defaults(handler="prompt")

    vault_health = subparsers.add_parser(
        "vault-health",
        help="Scan the configured Obsidian vault without modifying it.",
    )
    vault_health.add_argument(
        "--vault-path",
        type=Path,
        help="Optional read-only override for the configured vault path.",
    )
    vault_health.add_argument(
        "--stale-days",
        type=positive_int,
        default=180,
        help="Flag notes not modified for at least this many days. Default: 180.",
    )
    vault_health.add_argument(
        "--review-limit",
        type=positive_int,
        default=10,
        help="Maximum high-potential notes to include. Default: 10.",
    )
    vault_health.add_argument(
        "--max-items",
        type=positive_int,
        default=20,
        help="Maximum items to print per human-readable section. Default: 20.",
    )
    vault_health.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print the full report as JSON.",
    )
    vault_health.set_defaults(handler="vault_health")

    query = subparsers.add_parser("query", help="Run a digital-twin query against the vault.")
    query.add_argument("question", help="The question to ask the digital twin.")
    query.add_argument(
        "--output-name",
        help="Optional filename under wiki/outputs/ when write-output is enabled.",
    )
    query.add_argument(
        "--write-output",
        action="store_true",
        help="Allow Codex to write the answer back to the vault outputs/log.",
    )
    query.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the prompt and command, but do not execute Codex.",
    )
    query.add_argument(
        "--language",
        choices=LANGUAGE_CHOICES,
        default="zh",
        help="Answer language for the assembled prompt. Default: zh.",
    )
    query.add_argument(
        "--model",
        help="Override the configured Codex model for this query invocation.",
    )
    query.set_defaults(handler="query")

    return parser


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]

    if args.handler == "demo":
        return run_demo(
            repo_root,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            html_output=args.html_output,
            save_html=args.save_html,
        )

    config = load_config(repo_root)

    if args.handler == "doctor":
        return run_doctor(config, json_output=args.json_output)

    if args.handler == "config":
        return run_config(config)

    if args.handler == "prompt":
        return run_prompt(
            config=config,
            question=args.question,
            language=args.language,
        )

    if args.handler == "vault_health":
        if args.vault_path:
            config = HarnessConfig(
                vault_path=args.vault_path,
                codex_path=config.codex_path,
                model=config.model,
                run_dir=config.run_dir,
            )
        return run_vault_health(
            config,
            stale_days=args.stale_days,
            review_limit=args.review_limit,
            max_items=args.max_items,
            json_output=args.json_output,
        )

    if args.handler == "query":
        return run_query(
            repo_root=repo_root,
            config=config,
            question=args.question,
            output_name=args.output_name,
            write_output=args.write_output,
            dry_run=args.dry_run,
            language=args.language,
            model=args.model,
        )

    parser.error(f"Unknown handler: {args.handler}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
