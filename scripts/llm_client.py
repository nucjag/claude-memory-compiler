from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class LLMResult:
    ok: bool
    provider: str
    text: str = ""
    cost_usd: float = 0.0
    error_type: str = ""
    error: str = ""


def _provider_order(cli_value: str | None) -> list[str]:
    raw = cli_value or os.environ.get("LLM_PROVIDER_ORDER", "claude,local")
    allowed = {"claude", "openai", "local"}
    items = [x.strip().lower() for x in raw.split(",") if x.strip()]
    normalized = [x for x in items if x in allowed]
    if not normalized:
        return ["claude", "local"]

    # Keep modes independent:
    # - claude,local
    # - openai,local
    # If both remotes are provided, keep only the first one.
    primary_remote = next((x for x in normalized if x in {"claude", "openai"}), "claude")
    has_local = "local" in normalized
    order = [primary_remote]
    if has_local:
        order.append("local")
    else:
        order.append("local")
    return order


def _timeout_s(cli_value: int | None) -> int:
    if cli_value and cli_value > 0:
        return cli_value
    try:
        return int(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))
    except ValueError:
        return 120


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
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(cwd),
            allowed_tools=[],
            max_turns=max_turns,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response += block.text
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
                async for message in query(
                    prompt=prompt,
                    options=ClaudeAgentOptions(
                        cwd=str(cwd),
                        system_prompt={"type": "preset", "preset": "claude_code"},
                        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
                        permission_mode="acceptEdits",
                        max_turns=30,
                    ),
                ):
                    if isinstance(message, ResultMessage):
                        cost = message.total_cost_usd or 0.0
                return LLMResult(ok=True, provider="claude", cost_usd=cost)

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
        except Exception as exc:
            last_error = LLMResult(
                ok=False,
                provider=provider,
                error_type=_classify_error(exc),
                error=str(exc),
            )
    return last_error
