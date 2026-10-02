"""
Delete leftover service sessions created by this compiler's Agent SDK calls.

Before `no-session-persistence` was added to llm_client.py, every compile/flush/lint/query
call left a full Claude Code transcript behind. This script finds those transcripts by the
first user message (the compiler's own prompts) and removes them with their companion
directories. Real sessions are never matched.

Usage:
    uv run python cleanup_sessions.py                 # dry run (default): list what would go
    uv run python cleanup_sessions.py --apply         # delete
    uv run python cleanup_sessions.py --projects-dir /path/to/projects --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path

from config import PROJECT_DIR, ROOT_DIR as WIKI_ROOT

SERVICE_PREFIXES = {
    "compile": "You are a knowledge compiler",
    "flush": "Review the conversation context below and respond with a concise summary",
    "lint": "Review this knowledge base for contradictions",
    "query-select": "You are selecting wiki",
    "query-answer": "You are a knowledge base",
}
TEST_PROMPTS = {"Reply ok", "Reply with the single word: ok", "ok"}
FLUSH_CONTEXT_MIN_AGE_S = 3600


def project_dir_name(path: Path) -> str:
    """Claude Code names a project directory after the cwd with non-alphanumerics replaced by '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def first_user_text(transcript: Path) -> str:
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = entry.get("message")
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                return content.strip()
    except OSError:
        pass
    return ""


def classify(text: str) -> str | None:
    for kind, prefix in SERVICE_PREFIXES.items():
        if text.startswith(prefix):
            return kind
    if text in TEST_PROMPTS:
        return "test"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete leftover compiler service sessions")
    parser.add_argument("--apply", action="store_true", help="Actually delete (default is a dry run)")
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "projects",
    )
    args = parser.parse_args()

    project_dirs = [args.projects_dir / project_dir_name(p) for p in {PROJECT_DIR, WIKI_ROOT}]
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    kept = 0
    to_delete: list[Path] = []

    for pdir in project_dirs:
        if not pdir.is_dir():
            continue
        for transcript in pdir.glob("*.jsonl"):
            kind = classify(first_user_text(transcript))
            if kind is None:
                kept += 1
                continue
            size = transcript.stat().st_size
            companion = transcript.with_suffix("")
            if companion.is_dir():
                size += sum(f.stat().st_size for f in companion.rglob("*") if f.is_file())
                to_delete.append(companion)
            to_delete.append(transcript)
            counts[kind] = counts.get(kind, 0) + 1
            sizes[kind] = sizes.get(kind, 0) + size

    scripts_dir = Path(__file__).resolve().parent
    now = time.time()
    stale_contexts = [
        f for f in scripts_dir.glob("session-flush-*.md") if now - f.stat().st_mtime > FLUSH_CONTEXT_MIN_AGE_S
    ]
    to_delete.extend(stale_contexts)

    print(f"{'APPLY' if args.apply else 'DRY RUN'}: projects dirs: {[str(d) for d in project_dirs if d.is_dir()]}")
    for kind in sorted(counts):
        print(f"  {kind:14s} {counts[kind]:6d} sessions  {sizes[kind] / 1e6:9.1f} MB")
    print(f"  stale session-flush-*.md files in scripts/: {len(stale_contexts)}")
    print(f"  kept (not matched, real sessions): {kept}")
    print(f"  total to delete: {sum(sizes.values()) / 1e6:.1f} MB in {sum(counts.values())} sessions")

    if not args.apply:
        print("Nothing deleted. Re-run with --apply.")
        return

    for path in to_delete:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    print("Deleted.")


if __name__ == "__main__":
    main()
