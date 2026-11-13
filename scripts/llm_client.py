from __future__ import annotations

import asyncio
import json
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import CLAUDE_MODEL, ROOT_DIR

DEFAULT_CODEX_MODEL = "gpt-5.6-sol"

def isolated_session_options(stderr_lines: list[str]) -> dict[str, Any]:
    """Options that keep a service session from loading hooks/MCP or leaving a transcript.

    Child sessions must not load hooks or MCP servers, otherwise their SessionEnd
    re-triggers compile.py. SDK 0.1.56 drops `setting_sources=[]` (falsy), so the CLI
    flags go through extra_args. `stderr` is needed because the SDK otherwise reports
    only "Check stderr output for details".
    """
    return {
        "env": {"CLAUDE_INVOKED_BY": "memory_compiler"},
        "extra_args": {
            "strict-mcp-config": None,
            "setting-sources": "",
            "no-session-persistence": None,
        },
        "stderr": stderr_lines.append,
    }


def _with_stderr(exc: Exception, stderr_lines: list[str]) -> str:
    tail = " ".join(line.strip() for line in stderr_lines[-5:])[:500]
    return f"{exc} | stderr: {tail}" if tail else str(exc)


@dataclass
class LLMResult:
    ok: bool
    provider: str
    text: str = ""
    cost_usd: float = 0.0
    error_type: str = ""
    error: str = ""
    subtype: str = ""
    num_turns: int = 0


def _provider_order(cli_value: str | None) -> list[str]:
    raw = cli_value or os.environ.get("LLM_PROVIDER_ORDER", "codex,local")
    allowed = {"claude", "openai", "codex", "local"}
    items = [x.strip().lower() for x in raw.split(",") if x.strip()]
    normalized = [x for x in items if x in allowed]
    if not normalized:
        return ["codex", "local"]

    return list(dict.fromkeys(x for x in normalized if x != "local")) + ["local"]


def _timeout_s(cli_value: int | None) -> int:
    if cli_value and cli_value > 0:
        return cli_value
    try:
        return int(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))
    except ValueError:
        return 120


def _codex_timeout_s(cli_value: int | None) -> int:
    if cli_value and cli_value > 0:
        return cli_value
    try:
        return int(os.environ.get("LLM_TIMEOUT_SECONDS", "600"))
    except ValueError:
        return 600


def _classify_error(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in text:
        return "LLM_TIMEOUT"
    if "auth" in text or "credential" in text or "unauthorized" in text:
        return "LLM_AUTH"
    if "quota" in text or "rate limit" in text or "429" in text:
        return "LLM_QUOTA"
    return "LLM_UNAVAILABLE"


async def _run_claude_text(prompt: str, cwd: Path, max_turns: int) -> LLMResult:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    response = ""
    stderr_lines: list[str] = []
    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(cwd),
                model=CLAUDE_MODEL,
                allowed_tools=[],
                max_turns=max_turns,
                **isolated_session_options(stderr_lines),
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response += block.text
    except Exception as exc:
        raise RuntimeError(_with_stderr(exc, stderr_lines)) from exc
    return LLMResult(ok=True, provider="claude", text=response)


async def _run_openai_text(prompt: str, model: str, max_tokens: int = 4000) -> LLMResult:
    from openai import OpenAI

    client = OpenAI()
    resp = client.responses.create(
        model=model,
        input=prompt,
        max_output_tokens=max_tokens,
    )
    text = resp.output_text or ""
    return LLMResult(ok=True, provider="openai", text=text)


def _codex_model() -> str:
    """Resolve the wiki compiler model independently of Codex user settings."""
    override = os.environ.get("LLM_CODEX_MODEL", "").strip()
    if override:
        return override
    config_path = ROOT_DIR / "config.toml"
    if config_path.exists():
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
        model = config.get("llm", {}).get("codex_model", DEFAULT_CODEX_MODEL)
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"llm.codex_model must be a non-empty string in {config_path}")
        return model.strip()
    return DEFAULT_CODEX_MODEL


async def _run_codex(prompt: str, cwd: Path, *, writable: bool) -> LLMResult:
    """Use the signed-in Codex CLI; keep service runs free of project hooks."""
    binary = shutil.which("codex")
    if not binary:
        raise RuntimeError("codex CLI is not installed")
    command = [binary, "exec", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
               "--model", _codex_model(),
               "--sandbox", "workspace-write" if writable else "read-only",
               "-C", str(cwd), "-"]
    env = {**os.environ, "CLAUDE_INVOKED_BY": "memory_compiler"}
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await process.communicate(prompt.encode("utf-8"))
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(f"codex exec exited {process.returncode}: {stderr.decode('utf-8', 'replace')[-1000:]}")
    result = stdout.decode("utf-8", "replace").strip()
    if not result:
        raise RuntimeError("codex exec returned an empty response")
    return LLMResult(ok=True, provider="codex", text=result)


def _apply_file_operations(operations: list[dict[str, Any]], root_dir: Path) -> None:
    for op in operations:
        kind = op.get("op")
        path_raw = op.get("path", "")
        if not path_raw:
            continue
        path = Path(path_raw)
        if not path.is_absolute():
            path = root_dir / path
        path = path.resolve()
        if not str(path).startswith(str(root_dir.resolve())):
            raise ValueError(f"refusing to write outside root: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "write":
            path.write_text(op.get("content", ""), encoding="utf-8")
        elif kind == "append":
            with open(path, "a", encoding="utf-8") as f:
                f.write(op.get("content", ""))
        else:
            raise ValueError(f"unknown op: {kind}")


async def run_text_with_fallback(
    *,
    prompt: str,
    cwd: Path,
    provider_order: str | None,
    timeout_s: int | None,
    openai_model: str | None,
    max_turns: int = 2,
) -> LLMResult:
    order = _provider_order(provider_order)
    timeout_seconds = _timeout_s(timeout_s)
    openai_model_value = openai_model or os.environ.get("LLM_OPENAI_MODEL", "gpt-5.4")
    last_error = LLMResult(ok=False, provider="local", error_type="LLM_UNAVAILABLE", error="all providers failed")

    for provider in order:
        if provider == "local":
            return LLMResult(ok=False, provider="local", error_type=last_error.error_type, error=last_error.error)
        try:
            if provider == "claude":
                return await asyncio.wait_for(
                    _run_claude_text(prompt=prompt, cwd=cwd, max_turns=max_turns),
                    timeout=timeout_seconds,
                )
            if provider == "openai":
                return await asyncio.wait_for(
                    _run_openai_text(prompt=prompt, model=openai_model_value),
                    timeout=timeout_seconds,
                )
            if provider == "codex":
                return await asyncio.wait_for(
                    _run_codex(prompt, cwd, writable=False), timeout=_codex_timeout_s(timeout_s),
                )
        except Exception as exc:
            last_error = LLMResult(
                ok=False,
                provider=provider,
                error_type=_classify_error(exc),
                error=str(exc),
            )
    return last_error


async def run_compile_with_fallback(
    *,
    prompt: str,
    cwd: Path,
    root_dir: Path,
    provider_order: str | None,
    timeout_s: int | None,
    openai_model: str | None,
) -> LLMResult:
    order = _provider_order(provider_order)
    timeout_seconds = _timeout_s(timeout_s)
    openai_model_value = openai_model or os.environ.get("LLM_OPENAI_MODEL", "gpt-5.4")
    last_error = LLMResult(ok=False, provider="local", error_type="LLM_UNAVAILABLE", error="all providers failed")

    for provider in order:
        if provider == "local":
            return LLMResult(ok=False, provider="local", error_type=last_error.error_type, error=last_error.error)
        try:
            if provider == "claude":
                from claude_agent_sdk import (
                    ClaudeAgentOptions,
                    ResultMessage,
                    query,
                )

                cost = 0.0
                subtype = ""
                is_error = False
                num_turns = 0
                stderr_lines: list[str] = []
                try:
                    async for message in query(
                        prompt=prompt,
                        options=ClaudeAgentOptions(
                            cwd=str(cwd),
                            model=CLAUDE_MODEL,
                            system_prompt={"type": "preset", "preset": "claude_code"},
                            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
                            permission_mode="acceptEdits",
                            max_turns=30,
                            **isolated_session_options(stderr_lines),
                        ),
                    ):
                        if isinstance(message, ResultMessage):
                            cost = message.total_cost_usd or 0.0
                            subtype = message.subtype
                            is_error = bool(message.is_error)
                            num_turns = message.num_turns
                except Exception as exc:
                    raise RuntimeError(_with_stderr(exc, stderr_lines)) from exc
                if is_error:
                    return LLMResult(
                        ok=False,
                        provider="claude",
                        cost_usd=cost,
                        error_type="LLM_SESSION_ERROR",
                        error=f"session ended with {subtype}",
                        subtype=subtype,
                        num_turns=num_turns,
                    )
                return LLMResult(ok=True, provider="claude", cost_usd=cost, subtype=subtype, num_turns=num_turns)

            if provider == "openai":
                compile_protocol = f"""{prompt}

Return ONLY valid JSON (no markdown fences), schema:
{{
  "operations": [
    {{"op":"write","path":"relative/or/absolute/path.md","content":"full content"}},
    {{"op":"append","path":"relative/or/absolute/path.md","content":"text to append"}}
  ]
}}
"""
                response = await _run_openai_text(prompt=compile_protocol, model=openai_model_value, max_tokens=12000)
                payload = json.loads(response.text.strip())
                operations = payload.get("operations", [])
                if not isinstance(operations, list):
                    raise ValueError("openai response has invalid operations")
                _apply_file_operations(operations, root_dir=root_dir)
                return LLMResult(ok=True, provider="openai", text="operations_applied")
            if provider == "codex":
                codex_prompt = f"{prompt}\n\nWork only in {root_dir}. Complete all required file edits. " \
                    "Do not run the compiler, flush hooks, or unrelated commands. " \
                    "Do not claim completion unless the files were written."
                return await asyncio.wait_for(
                    _run_codex(codex_prompt, root_dir, writable=True), timeout=_codex_timeout_s(timeout_s),
                )
        except Exception as exc:
            last_error = LLMResult(
                ok=False,
                provider=provider,
                error_type=_classify_error(exc),
                error=str(exc),
            )
    return last_error
