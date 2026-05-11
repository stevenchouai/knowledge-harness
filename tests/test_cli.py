from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_harness import cli
from knowledge_harness import vault_health


def write_harness_config(config_dir: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "vault_path": "~/vault",
        "codex_path": "~/bin/codex",
        "model": "test-model",
        "run_dir": "~/runs",
    }
    payload.update(overrides)
    config_dir.mkdir(parents=True)
    path = config_dir / "harness.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class LoadConfigTests(unittest.TestCase):
    def test_load_config_reports_invalid_json_with_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config" / "harness.json"
            config_path.parent.mkdir()
            config_path.write_text('{"vault_path": ', encoding="utf-8")

            with self.assertRaises(SystemExit) as raised:
                cli.load_config(root)

            message = str(raised.exception)
            self.assertIn(str(config_path), message)
            self.assertIn("Invalid JSON", message)
            self.assertIn("line", message)

    def test_load_config_reports_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config" / "harness.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps(
                    {
                        "vault_path": "~/vault",
                        "codex_path": "~/bin/codex",
                        "model": "test-model",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit) as raised:
                cli.load_config(root)

            message = str(raised.exception)
            self.assertIn(str(config_path), message)
            self.assertIn("missing required field", message)
            self.assertIn("run_dir", message)

    def test_load_config_reports_config_read_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = write_harness_config(root / "config")

            with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
                with self.assertRaises(SystemExit) as raised:
                    cli.load_config(root)

            message = str(raised.exception)
            self.assertIn(str(config_path), message)
            self.assertIn("Could not read config file", message)
            self.assertIn("permission denied", message)

    def test_load_config_reports_non_object_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "config" / "harness.json"
            config_path.parent.mkdir()
            config_path.write_text("[]", encoding="utf-8")

            with self.assertRaises(SystemExit) as raised:
                cli.load_config(root)

            message = str(raised.exception)
            self.assertIn(str(config_path), message)
            self.assertIn("must contain a JSON object", message)

    def test_load_config_reports_non_string_values_by_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = write_harness_config(root / "config", model=["not", "string"])

            with self.assertRaises(SystemExit) as raised:
                cli.load_config(root)

            message = str(raised.exception)
            self.assertIn(str(config_path), message)
            self.assertIn("'model'", message)
            self.assertIn("must be a string", message)

    def test_load_config_expands_paths_from_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_harness_config(root / "config")

            config = cli.load_config(root)

            self.assertEqual(config.vault_path, Path.home() / "vault")
            self.assertEqual(config.codex_path, Path.home() / "bin" / "codex")
            self.assertEqual(config.model, "test-model")
            self.assertEqual(config.run_dir, Path.home() / "runs")


class RunQueryDryRunValidationTests(unittest.TestCase):
    def make_vault(self, root: Path) -> Path:
        vault = root / "vault"
        wiki = vault / "wiki"
        wiki.mkdir(parents=True)
        (vault / "AGENTS.md").write_text("agent contract", encoding="utf-8")
        (vault / "PROMPTS.md").write_text("prompt contract", encoding="utf-8")
        (wiki / "_index.md").write_text("routing index", encoding="utf-8")
        return vault

    def make_config(self, root: Path, vault: Path) -> cli.HarnessConfig:
        return cli.HarnessConfig(
            vault_path=vault,
            codex_path=root / "missing-codex",
            model="test-model",
            run_dir=root / "runs",
        )

    def test_dry_run_does_not_require_codex_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            config = self.make_config(root, self.make_vault(root))

            with redirect_stdout(io.StringIO()):
                result = cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                    dry_run=True,
                )

            self.assertEqual(result, 0)
            run_dirs = list(config.run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_path = run_dirs[0]
            self.assertTrue((run_path / "prompt.txt").exists())

            metadata = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], 1)
            self.assertTrue(metadata["dry_run"])
            self.assertEqual(metadata["language"], "zh")
            self.assertEqual(metadata["model"], "test-model")
            self.assertEqual(metadata["command"][0], str(config.codex_path))
            model_index = metadata["command"].index("--model")
            self.assertEqual(metadata["command"][model_index + 1], "test-model")

    def test_query_model_override_updates_command_and_metadata_for_one_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            config = self.make_config(root, self.make_vault(root))

            with redirect_stdout(io.StringIO()):
                result = cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                    dry_run=True,
                    model="override-model",
                )

            self.assertEqual(result, 0)
            self.assertEqual(config.model, "test-model")
            run_dirs = list(config.run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            metadata = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["model"], "override-model")
            model_index = metadata["command"].index("--model")
            self.assertEqual(metadata["command"][model_index + 1], "override-model")

    def test_dry_run_does_not_modify_fake_vault_or_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            vault = self.make_vault(root)
            config = self.make_config(root, vault)
            before = snapshot_files(vault)

            with redirect_stdout(io.StringIO()):
                result = cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="How does this harness route a public-safe question?",
                    output_name=None,
                    write_output=False,
                    dry_run=True,
                    language="en",
                )

            self.assertEqual(result, 0)
            self.assertEqual(snapshot_files(vault), before)
            run_dirs = list(config.run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            self.assertFalse((run_dirs[0] / "last_message.txt").exists())
            metadata = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], 1)
            self.assertTrue(metadata["dry_run"])
            self.assertFalse(metadata["write_output"])
            self.assertEqual(metadata["language"], "en")
            self.assertNotIn("exit_code", metadata)

    def test_non_write_query_does_not_grant_vault_write_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            config = self.make_config(root, self.make_vault(root))
            output_path = root / "runs" / "last_message.txt"

            command = cli.build_codex_command(
                repo_root,
                config,
                output_path,
                write_output=False,
            )

            self.assertIn("workspace-write", command)
            self.assertNotIn("danger-full-access", command)
            self.assertNotIn("--add-dir", command)
            self.assertNotIn(str(config.vault_path), command)

    def test_write_output_query_explicitly_grants_vault_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            config = self.make_config(root, self.make_vault(root))
            output_path = root / "runs" / "last_message.txt"

            command = cli.build_codex_command(
                repo_root,
                config,
                output_path,
                write_output=True,
            )

            self.assertIn("workspace-write", command)
            self.assertNotIn("danger-full-access", command)
            add_dir_index = command.index("--add-dir")
            self.assertEqual(command[add_dir_index + 1], str(config.vault_path))

    def test_query_defaults_to_chinese_prompt_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            config = self.make_config(root, self.make_vault(root))

            with redirect_stdout(io.StringIO()):
                result = cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                    dry_run=True,
                )

            self.assertEqual(result, 0)
            run_dirs = list(config.run_dir.iterdir())
            prompt = (run_dirs[0] / "prompt.txt").read_text(encoding="utf-8")
            self.assertIn("Answer in Chinese.", prompt)
            self.assertIn("Structure: 先结论 -> 再依据 -> 再下一步建议.", prompt)

    def test_query_accepts_english_prompt_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            config = self.make_config(root, self.make_vault(root))

            with redirect_stdout(io.StringIO()):
                result = cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                    dry_run=True,
                    language="en",
                )

            self.assertEqual(result, 0)
            run_dirs = list(config.run_dir.iterdir())
            prompt = (run_dirs[0] / "prompt.txt").read_text(encoding="utf-8")
            metadata = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertIn("Answer in English.", prompt)
            self.assertIn(
                "Structure: conclusion first -> evidence next -> suggested next steps.",
                prompt,
            )
            self.assertNotIn("Answer in Chinese.", prompt)
            self.assertEqual(metadata["language"], "en")

    def test_dry_run_still_requires_vault_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            vault = root / "vault"
            (vault / "wiki").mkdir(parents=True)
            (vault / "AGENTS.md").write_text("agent contract", encoding="utf-8")
            config = self.make_config(root, vault)

            with self.assertRaises(SystemExit) as raised:
                cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                    dry_run=True,
                )

            message = str(raised.exception)
            self.assertIn("Missing required knowledge-base files", message)
            self.assertIn(str(vault), message)
            self.assertIn("- PROMPTS.md", message)
            self.assertIn("- wiki/_index.md", message)
            self.assertFalse(config.run_dir.exists())

    def test_doctor_requires_codex_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self.make_config(root, self.make_vault(root))

            with self.assertRaises(SystemExit) as raised:
                cli.run_doctor(config)

            self.assertIn("Codex binary not found", str(raised.exception))

    def test_doctor_json_reports_status_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self.make_config(root, self.make_vault(root))

            output = io.StringIO()
            with redirect_stdout(output):
                result = cli.run_doctor(config, json_output=True)

            self.assertEqual(result, 1)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["vault_ok"])
            self.assertEqual(payload["missing_vault_files"], [])
            self.assertEqual(payload["codex_path"], str(config.codex_path))
            self.assertFalse(payload["codex_ok"])
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(payload["run_dir"], str(config.run_dir))

    def test_doctor_json_returns_zero_when_required_paths_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex = root / "fake-codex"
            codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            codex.chmod(0o755)
            config = cli.HarnessConfig(
                vault_path=self.make_vault(root),
                codex_path=codex,
                model="test-model",
                run_dir=root / "runs",
            )

            with redirect_stdout(io.StringIO()):
                result = cli.run_doctor(config, json_output=True)

            self.assertEqual(result, 0)

    def test_parser_accepts_doctor_json(self) -> None:
        args = cli.build_parser().parse_args(["doctor", "--json"])

        self.assertEqual(args.handler, "doctor")
        self.assertTrue(args.json_output)

    def test_parser_accepts_query_language(self) -> None:
        args = cli.build_parser().parse_args(["query", "question", "--language", "en"])

        self.assertEqual(args.handler, "query")
        self.assertEqual(args.language, "en")

    def test_parser_accepts_query_model_override(self) -> None:
        args = cli.build_parser().parse_args(["query", "question", "--model", "model-x"])

        self.assertEqual(args.handler, "query")
        self.assertEqual(args.model, "model-x")

    def test_parser_rejects_invalid_query_language(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["query", "question", "--language", "fr"])

    def test_real_query_requires_codex_binary_before_creating_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            config = self.make_config(root, self.make_vault(root))

            with self.assertRaises(SystemExit) as raised:
                cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                    dry_run=False,
                )

            self.assertIn("Codex binary not found", str(raised.exception))
            self.assertFalse(config.run_dir.exists())

    def test_query_rejects_unsafe_output_name_before_creating_run(self) -> None:
        unsafe_names = [
            "",
            ".",
            "..",
            "../answer.md",
            "safe/../answer.md",
            "outputs/answer.md",
            "outputs\\answer.md",
            "/tmp/answer.md",
            "answer.txt",
        ]
        for output_name in unsafe_names:
            with self.subTest(output_name=output_name):
                with tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    repo_root = root / "repo"
                    repo_root.mkdir()
                    config = self.make_config(root, self.make_vault(root))

                    with self.assertRaises(SystemExit):
                        cli.run_query(
                            repo_root=repo_root,
                            config=config,
                            question="What should the harness do next?",
                            output_name=output_name,
                            write_output=True,
                            dry_run=True,
                        )

                    self.assertFalse(config.run_dir.exists())

    def test_query_accepts_markdown_output_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            config = self.make_config(root, self.make_vault(root))

            with redirect_stdout(io.StringIO()):
                result = cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="What should the harness do next?",
                    output_name="answer.md",
                    write_output=True,
                    dry_run=True,
                )

            self.assertEqual(result, 0)
            run_dirs = list(config.run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            prompt = (run_dirs[0] / "prompt.txt").read_text(encoding="utf-8")
            self.assertIn("wiki/outputs/answer.md", prompt)

    def test_real_query_records_subprocess_exit_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            codex = root / "fake-codex"
            codex.write_text("#!/bin/sh\ncat >/dev/null\nexit 7\n", encoding="utf-8")
            codex.chmod(0o755)
            config = cli.HarnessConfig(
                vault_path=self.make_vault(root),
                codex_path=codex,
                model="test-model",
                run_dir=root / "runs",
            )

            with redirect_stdout(io.StringIO()):
                result = cli.run_query(
                    repo_root=repo_root,
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                    dry_run=False,
                )

            self.assertEqual(result, 7)
            run_dirs = list(config.run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            metadata = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], 1)
            self.assertEqual(metadata["exit_code"], 7)
            self.assertFalse(metadata["dry_run"])
            self.assertEqual(metadata["question"], "What should the harness do next?")
            self.assertIn("ended_at", metadata)
            self.assertIsNotNone(datetime.fromisoformat(metadata["ended_at"]))


class ConfigCommandTests(unittest.TestCase):
    def test_config_prints_effective_config_without_validating_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = cli.HarnessConfig(
                vault_path=root / "missing-vault",
                codex_path=root / "missing-codex",
                model="test-model",
                run_dir=root / "missing-runs",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = cli.run_config(config)

            self.assertEqual(result, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["vault_path"], str(config.vault_path))
            self.assertEqual(payload["codex_path"], str(config.codex_path))
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(payload["run_dir"], str(config.run_dir))
            self.assertFalse(config.run_dir.exists())

    def test_parser_accepts_config_subcommand(self) -> None:
        args = cli.build_parser().parse_args(["config"])

        self.assertEqual(args.handler, "config")


class DemoCommandTests(unittest.TestCase):
    def test_parser_accepts_demo_subcommand(self) -> None:
        args = cli.build_parser().parse_args(["demo"])

        self.assertEqual(args.handler, "demo")
        self.assertFalse(args.json_output)

    def test_parser_accepts_demo_json(self) -> None:
        args = cli.build_parser().parse_args(["demo", "--json"])

        self.assertEqual(args.handler, "demo")
        self.assertTrue(args.json_output)

    def test_main_demo_bypasses_local_config_loading(self) -> None:
        with patch.object(cli, "load_config", side_effect=AssertionError("loaded config")):
            with patch.object(cli, "run_demo", return_value=0) as run_demo:
                result = cli.main(["demo"])

        self.assertEqual(result, 0)
        run_demo.assert_called_once()

    def test_main_demo_json_bypasses_local_config_loading(self) -> None:
        with patch.object(cli, "load_config", side_effect=AssertionError("loaded config")):
            with patch.object(cli, "run_demo", return_value=0) as run_demo:
                result = cli.main(["demo", "--json"])

        self.assertEqual(result, 0)
        run_demo.assert_called_once()
        self.assertTrue(run_demo.call_args.kwargs["json_output"])

    def test_demo_creates_temporary_fake_vault_prompt_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            demo_root = root / "demo"

            output = io.StringIO()
            with redirect_stdout(output):
                result = cli.run_demo(repo_root=repo_root, demo_root=demo_root)

            self.assertEqual(result, 0)
            self.assertIn("status: no real vault or Codex was used", output.getvalue())

            fake_vault = demo_root / "fake-vault"
            run_root = demo_root / "runs"
            self.assertEqual(
                sorted(
                    path.relative_to(fake_vault).as_posix()
                    for path in fake_vault.rglob("*")
                    if path.is_file()
                ),
                ["AGENTS.md", "PROMPTS.md", "wiki/_index.md"],
            )

            run_dirs = [path for path in run_root.iterdir() if path.is_dir()]
            self.assertEqual(len(run_dirs), 1)
            run_path = run_dirs[0]
            prompt_path = run_path / "prompt.txt"
            metadata_path = run_path / "run.json"
            self.assertTrue(prompt_path.exists())
            self.assertTrue(metadata_path.exists())
            self.assertFalse((run_path / "last_message.txt").exists())

            prompt = prompt_path.read_text(encoding="utf-8")
            self.assertIn("Use this fake vault only for public demos.", prompt)
            self.assertIn("Route public demo questions through the fake wiki index.", prompt)
            self.assertIn("[[public-demo]] explains the sanitized demo flow.", prompt)
            self.assertIn("Answer in English.", prompt)

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertTrue(metadata["dry_run"])
            self.assertFalse(metadata["write_output"])
            self.assertEqual(metadata["language"], "en")
            self.assertEqual(metadata["question"], cli.DEMO_QUESTION)
            self.assertEqual(metadata["command"][0], str(demo_root / "missing-codex"))
            self.assertIn("workspace-write", metadata["command"])
            self.assertNotIn("danger-full-access", metadata["command"])
            self.assertNotIn("--add-dir", metadata["command"])
            self.assertNotIn(str(fake_vault), metadata["command"])
            self.assertFalse((demo_root / "missing-codex").exists())

    def test_demo_json_prints_machine_readable_safe_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            repo_root.mkdir()
            demo_root = root / "demo with spaces"

            output = io.StringIO()
            with redirect_stdout(output):
                result = cli.run_demo(
                    repo_root=repo_root,
                    demo_root=demo_root,
                    json_output=True,
                )

            self.assertEqual(result, 0)
            receipt = json.loads(output.getvalue())
            fake_vault = demo_root / "fake-vault"
            run_path = Path(receipt["run_dir"])
            prompt_path = Path(receipt["prompt_file"])
            metadata_path = Path(receipt["metadata_file"])

            self.assertEqual(receipt["fake_vault"], str(fake_vault))
            self.assertEqual(prompt_path, run_path / "prompt.txt")
            self.assertEqual(metadata_path, run_path / "run.json")
            self.assertEqual(receipt["status"], "no real vault or Codex was used")
            self.assertFalse(receipt["real_vault_used"])
            self.assertFalse(receipt["codex_used"])
            self.assertFalse(receipt["write_output"])
            self.assertTrue(receipt["dry_run"])
            self.assertEqual(receipt["cleanup_root"], str(demo_root))
            self.assertEqual(
                receipt["cleanup_command"], f"rm -rf {shlex.quote(str(demo_root))}"
            )

            for key in ("fake_vault", "run_dir", "prompt_file", "metadata_file"):
                Path(receipt[key]).relative_to(demo_root)

            self.assertEqual(
                sorted(
                    path.relative_to(fake_vault).as_posix()
                    for path in fake_vault.rglob("*")
                    if path.is_file()
                ),
                ["AGENTS.md", "PROMPTS.md", "wiki/_index.md"],
            )
            self.assertTrue(prompt_path.exists())
            self.assertTrue(metadata_path.exists())
            self.assertFalse((run_path / "last_message.txt").exists())

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertTrue(metadata["dry_run"])
            self.assertFalse(metadata["write_output"])
            self.assertNotIn("--add-dir", metadata["command"])
            self.assertNotIn(str(fake_vault), metadata["command"])

    def test_module_demo_json_invocation_does_not_need_config_or_codex(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(repo_root / "src")
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        proc = subprocess.run(
            [sys.executable, "-m", "knowledge_harness.cli", "demo", "--json"],
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = json.loads(proc.stdout)
        self.assertFalse(receipt["real_vault_used"])
        self.assertFalse(receipt["codex_used"])
        self.assertFalse(receipt["write_output"])
        self.assertTrue(receipt["dry_run"])
        self.assertTrue(Path(receipt["prompt_file"]).exists())
        self.assertTrue(Path(receipt["metadata_file"]).exists())
        shutil.rmtree(Path(receipt["fake_vault"]).parent)

    def test_demo_does_not_mutate_local_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            config_path = repo_root / "config" / "harness.json"
            config_path.parent.mkdir(parents=True)
            config_text = json.dumps(
                {
                    "vault_path": "/example/private-vault",
                    "codex_path": "/example/missing-codex",
                    "model": "local-model",
                    "run_dir": "/example/runs",
                },
                indent=2,
            )
            config_path.write_text(config_text, encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                result = cli.run_demo(repo_root=repo_root, demo_root=root / "demo")

            self.assertEqual(result, 0)
            self.assertEqual(config_path.read_text(encoding="utf-8"), config_text)


class PromptCommandTests(unittest.TestCase):
    def make_vault(self, root: Path) -> Path:
        vault = root / "vault"
        wiki = vault / "wiki"
        wiki.mkdir(parents=True)
        (vault / "AGENTS.md").write_text("agent contract", encoding="utf-8")
        (vault / "PROMPTS.md").write_text("prompt contract", encoding="utf-8")
        (wiki / "_index.md").write_text("routing index", encoding="utf-8")
        return vault

    def test_prompt_prints_assembled_prompt_without_creating_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = cli.HarnessConfig(
                vault_path=self.make_vault(root),
                codex_path=root / "missing-codex",
                model="test-model",
                run_dir=root / "runs",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = cli.run_prompt(
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                )

            prompt = output.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("What should the harness do next?", prompt)
            self.assertIn("Answer in Chinese.", prompt)
            self.assertIn("agent contract", prompt)
            self.assertIn("prompt contract", prompt)
            self.assertIn("routing index", prompt)
            self.assertFalse(config.run_dir.exists())

    def test_prompt_accepts_english_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = cli.HarnessConfig(
                vault_path=self.make_vault(root),
                codex_path=root / "missing-codex",
                model="test-model",
                run_dir=root / "runs",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = cli.run_prompt(
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=False,
                    language="en",
                )

            prompt = output.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("Answer in English.", prompt)
            self.assertIn(
                "Structure: conclusion first -> evidence next -> suggested next steps.",
                prompt,
            )
            self.assertNotIn("Answer in Chinese.", prompt)
            self.assertFalse(config.run_dir.exists())

    def test_prompt_write_output_requires_output_name_for_write_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = cli.HarnessConfig(
                vault_path=self.make_vault(root),
                codex_path=root / "missing-codex",
                model="test-model",
                run_dir=root / "runs",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                result = cli.run_prompt(
                    config=config,
                    question="What should the harness do next?",
                    output_name=None,
                    write_output=True,
                )

            self.assertEqual(result, 0)
            self.assertIn("Do not write files unless needed", output.getvalue())
            self.assertNotIn("wiki/outputs", output.getvalue())
            self.assertFalse(config.run_dir.exists())

    def test_prompt_validates_output_name_before_printing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = cli.HarnessConfig(
                vault_path=self.make_vault(root),
                codex_path=root / "missing-codex",
                model="test-model",
                run_dir=root / "runs",
            )

            with self.assertRaises(SystemExit):
                cli.run_prompt(
                    config=config,
                    question="What should the harness do next?",
                    output_name="../escape.md",
                    write_output=True,
                )

            self.assertFalse(config.run_dir.exists())

    def test_parser_accepts_prompt_subcommand(self) -> None:
        args = cli.build_parser().parse_args(
            [
                "prompt",
                "question",
                "--write-output",
                "--output-name",
                "answer.md",
                "--language",
                "en",
            ]
        )

        self.assertEqual(args.handler, "prompt")
        self.assertEqual(args.question, "question")
        self.assertTrue(args.write_output)
        self.assertEqual(args.output_name, "answer.md")
        self.assertEqual(args.language, "en")

    def test_parser_rejects_invalid_prompt_language(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["prompt", "question", "--language", "fr"])


class VaultHealthTests(unittest.TestCase):
    fixed_now = datetime(2026, 5, 3, tzinfo=timezone.utc)

    def write_note(
        self,
        vault: Path,
        rel_path: str,
        content: str,
        *,
        days_old: int = 1,
    ) -> Path:
        path = vault / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        modified_at = self.fixed_now - timedelta(days=days_old)
        os.utime(path, (modified_at.timestamp(), modified_at.timestamp()))
        return path

    def make_health_vault(self, root: Path) -> Path:
        vault = root / "vault"
        vault.mkdir()
        self.write_note(
            vault,
            "Home.md",
            "Links to [[Linked]] and [[Folder/Deep]] and [[Missing Note]].\n"
            "Attachment links like ![[diagram.png]] are ignored.\n",
        )
        self.write_note(vault, "Linked.md", "Back to [[Home]].\n")
        self.write_note(vault, "Folder/Deep.md", "Deep note.\n")
        self.write_note(vault, "Orphan.md", "No links here.\n")
        self.write_note(vault, "Empty.md", "---\ntags: [draft]\n---\n\n")
        self.write_note(vault, "Ideas.md", "First title.\n")
        self.write_note(vault, "Archive/ideas.md", "Duplicate title by case.\n")
        self.write_note(vault, "Topic A.md", "A note.\n")
        self.write_note(vault, "Topic B.md", "B note.\n")
        self.write_note(vault, "Topic C.md", "C note.\n")
        self.write_note(
            vault,
            "Hub.md",
            "Review surface with [[Topic A]], [[Topic B]], and [[Topic C]].\n",
            days_old=240,
        )
        (vault / ".obsidian").mkdir()
        self.write_note(vault, ".obsidian/Internal.md", "[[Missing Internal]]")
        return vault

    def test_scan_reports_expected_vault_health_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = self.make_health_vault(Path(tmpdir))

            report = vault_health.scan_vault_health(
                vault,
                stale_days=180,
                high_potential_limit=5,
                now=self.fixed_now,
            )

            self.assertEqual(report.note_count, 11)
            self.assertIn("Orphan.md", report.orphan_notes)
            self.assertIn("Empty.md", report.empty_notes)

            broken = {(link.source_path, link.target) for link in report.broken_wikilinks}
            self.assertEqual(broken, {("Home.md", "Missing Note")})

            duplicate_titles = {
                duplicate.title.casefold(): duplicate.paths
                for duplicate in report.duplicate_titles
            }
            self.assertEqual(
                duplicate_titles,
                {"ideas": ["Archive/ideas.md", "Ideas.md"]},
            )

            stale_paths = {note.path for note in report.stale_notes}
            self.assertEqual(stale_paths, {"Hub.md"})

            potential_paths = {note.path for note in report.high_potential_notes}
            self.assertIn("Hub.md", potential_paths)

    def test_vault_health_cli_is_read_only_and_does_not_create_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            vault = self.make_health_vault(root)
            config = cli.HarnessConfig(
                vault_path=vault,
                codex_path=root / "missing-codex",
                model="test-model",
                run_dir=root / "runs",
            )
            before = snapshot_files(vault)

            output = io.StringIO()
            with redirect_stdout(output):
                result = cli.run_vault_health(
                    config,
                    stale_days=180,
                    review_limit=5,
                    max_items=20,
                    json_output=False,
                )

            self.assertEqual(result, 0)
            self.assertIn("mode: read-only", output.getvalue())
            self.assertEqual(snapshot_files(vault), before)
            self.assertFalse(config.run_dir.exists())


def snapshot_files(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


if __name__ == "__main__":
    unittest.main()
