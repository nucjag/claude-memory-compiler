"""Codex CLI provider keeps compilation on the existing ChatGPT login."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from llm_client import _provider_order, _run_codex


def test_provider_order_selects_codex() -> None:
    assert _provider_order("codex,local") == ["codex", "local"]


def test_codex_exec_uses_cli_and_returns_final_answer(tmp_path: Path) -> None:
    commands = []

    class Process:
        returncode = 0

        async def communicate(self, prompt: bytes):
            assert prompt == b"compile this daily log"
            Path(commands[0][commands[0].index("--output-last-message") + 1]).write_text(
                "wiki updated", encoding="utf-8"
            )
            return b"", b""

    async def spawn(*command, **kwargs):
        commands.append(command)
        assert kwargs["cwd"] == str(tmp_path)
        return Process()

    with patch("llm_client.asyncio.create_subprocess_exec", side_effect=spawn):
        result = asyncio.run(_run_codex("compile this daily log", tmp_path, writable=True))

    assert result.ok and result.provider == "codex" and result.text == "wiki updated"
    assert commands[0][:5] == ("codex", "exec", "--ephemeral", "--sandbox", "workspace-write")


def test_codex_exec_failure_does_not_report_success(tmp_path: Path) -> None:
    class Process:
        returncode = 1

        async def communicate(self, prompt: bytes):
            return b"", b"authentication failed"

    async def spawn(*command, **kwargs):
        return Process()

    with patch("llm_client.asyncio.create_subprocess_exec", side_effect=spawn):
        try:
            asyncio.run(_run_codex("prompt", tmp_path, writable=False))
        except RuntimeError as exc:
            assert "authentication failed" in str(exc)
        else:
            raise AssertionError("failed Codex run must fail compilation")
