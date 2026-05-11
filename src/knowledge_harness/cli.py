from __future__ import annotations

import argparse
import io
import json
import os
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


def build_doctor_status(config: HarnessConfig) -> dict[str, object]:
    vault_files = [
        config.vault_path / "AGENTS.md",
        config.vault_path / "PROMPTS.md",
        config.vault_path / "wiki" / "_index.md",
    ]
    return {
        "vault_path": str(config.vault_path),
        "vault_ok": all(path.exists() for path in vault_files),
        "missing_vault_files": [str(path) for path in vault_files if not path.exists()],
        "codex_path": str(config.codex_path),
        "codex_ok": config.codex_path.exists(),
        "model": config.model,
        "run_dir": str(config.run_dir),
        "run_dir_ok": config.run_dir.exists() or config.run_dir.parent.exists(),
    }


def run_doctor(config: HarnessConfig, *, json_output: bool = False) -> int:
    if json_output:
        status = build_doctor_status(config)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["vault_ok"] and status["codex_ok"] else 1

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
    if command[:1] != [str(demo_root / "missing-codex")]:
        raise SystemExit(
            f"Demo metadata did not use the missing demo Codex path: {metadata_path}"
        )
    if "--add-dir" in command or str(fake_vault) in command:
        raise SystemExit(f"Demo metadata unexpectedly granted vault access: {metadata_path}")
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


def run_demo(
    repo_root: Path,
    demo_root: Path | None = None,
    *,
    json_output: bool = False,
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
    output_name: str | None,
    write_output: bool,
    language: str = "zh",
) -> int:
    output_name = validate_output_name(output_name)
    ensure_vault_contract(config)
    print(build_query_prompt(config, question, output_name, write_output, language))
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
    config.run_dir.mkdir(parents=True, exist_ok=True)
    stamp = make_run_stamp()
    run_path = config.run_dir / stamp
    run_path.mkdir(parents=True, exist_ok=True)

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

    metadata = {
        "schema_version": RUN_METADATA_SCHEMA_VERSION,
        "question": question,
        "output_name": output_name,
        "write_output": write_output,
        "dry_run": dry_run,
        "language": language,
        "model": config.model,
        "command": cmd,
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
    demo.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print a machine-readable proof receipt instead of human-readable output.",
    )
    demo.set_defaults(handler="demo")

    prompt = subparsers.add_parser(
        "prompt",
        help="Print the assembled query prompt without creating a run or calling Codex.",
    )
    prompt.add_argument("question", help="The question to ask the digital twin.")
    prompt.add_argument(
        "--output-name",
        help="Optional filename under wiki/outputs/ when write-output is enabled.",
    )
    prompt.add_argument(
        "--write-output",
        action="store_true",
        help="Include the vault write instruction in the printed prompt.",
    )
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
        return run_demo(repo_root, json_output=args.json_output)

    config = load_config(repo_root)

    if args.handler == "doctor":
        return run_doctor(config, json_output=args.json_output)

    if args.handler == "config":
        return run_config(config)

    if args.handler == "prompt":
        return run_prompt(
            config=config,
            question=args.question,
            output_name=args.output_name,
            write_output=args.write_output,
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
