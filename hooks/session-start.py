"""
SessionStart hook - injects knowledge base context into every conversation.

This is the "context injection" layer. When Claude Code starts a session,
this hook reads the knowledge base index and recent daily log, then injects
them as additional context so Claude always "remembers" what it has learned.

Configure in .claude/settings.json:
{
    "hooks": {
        "SessionStart": [{
            "matcher": "",
            "command": "uv run python hooks/session-start.py"
        }]
    }
}
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Recursion guard: skip context injection for memory-compiler child sessions
if os.environ.get("CLAUDE_INVOKED_BY"):
    sys.exit(0)

# Paths relative to active wiki root
PROJECT_DIR = Path(__file__).resolve().parents[4]
DEFAULT_ROOT = PROJECT_DIR / ".wiki"
root_env = os.environ.get("CLAUDE_WIKI_ROOT", "")
if root_env:
    root_candidate = Path(root_env).expanduser()
    if not root_candidate.is_absolute():
        root_candidate = PROJECT_DIR / root_candidate
    ROOT = root_candidate.resolve()
else:
    ROOT = DEFAULT_ROOT.resolve()
KNOWLEDGE_DIR = ROOT / "knowledge"
DAILY_DIR = ROOT / "daily"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"
TIMEZONE = os.environ.get("CLAUDE_WIKI_TIMEZONE", "Europe/Moscow")

# Claude Code caps additionalContext at 10,000 characters; anything longer is saved to a
# file and Claude sees only a 2,000-character preview. Stay below the cap with headroom.
MAX_CONTEXT_CHARS = 9_500
MAX_LOG_LINES = 30
MAX_LOG_CHARS = 3_000
# Summary widths tried in order until the compact index fits; 0 keeps article links only.
SUMMARY_WIDTHS = (None, *range(200, -1, -10))
INDEX_HINT = "Full index with summaries and sources: `{path}`"


def compact_index(index_content: str, width: int | None) -> str:
    """Index as headings plus `- [[article]] — summary` lines, summary cut to width."""
    lines = []
    for line in index_content.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and cells and cells[0].startswith("[["):
            summary = cells[1] if len(cells) > 1 else ""
            if width is not None and len(summary) > width:
                summary = summary[: max(width - 1, 0)].rstrip() + "…" if width else ""
            lines.append(f"- {cells[0]} — {summary}" if summary else f"- {cells[0]}")
        elif line.startswith("#") and not line.startswith("# "):
            lines.append(f"\n{line}")
    return "\n".join(lines).strip()


def get_recent_log() -> str:
    """Read the most recent daily log (today or yesterday)."""
    today = datetime.now(ZoneInfo(TIMEZONE))

    for offset in range(2):
        date = today - timedelta(days=offset)
        log_path = DAILY_DIR / f"{date.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            # Return last N lines to keep context small
            recent = lines[-MAX_LOG_LINES:] if len(lines) > MAX_LOG_LINES else lines
            return "\n".join(recent)[-MAX_LOG_CHARS:]

    return "(no recent daily log)"


def build_context() -> str:
    """Assemble the context to inject into the conversation."""
    today = datetime.now(ZoneInfo(TIMEZONE))
    head = f"## Today\n{today.strftime('%A, %B %d, %Y')}"
    tail = f"## Recent Daily Log\n\n{get_recent_log()}"

    def assemble(index_body: str) -> str:
        return "\n\n---\n\n".join([head, f"## Knowledge Base Index\n\n{index_body}", tail])

    # Knowledge base index (the core retrieval mechanism), shrunk until the whole context fits;
    # the date and the daily log are never cut.
    if not INDEX_FILE.exists():
        return assemble("(empty - no articles compiled yet)")[:MAX_CONTEXT_CHARS]

    index_content = INDEX_FILE.read_text(encoding="utf-8")
    hint = INDEX_HINT.format(path=INDEX_FILE.relative_to(PROJECT_DIR) if INDEX_FILE.is_relative_to(PROJECT_DIR) else INDEX_FILE)
    for width in SUMMARY_WIDTHS:
        context = assemble(f"{hint}\n\n{compact_index(index_content, width)}")
        if len(context) <= MAX_CONTEXT_CHARS:
            return context

    room = MAX_CONTEXT_CHARS - len(assemble(f"{hint}\n\n...(truncated)"))
    return assemble(f"{hint}\n\n{compact_index(index_content, 0)[: max(room, 0)]}\n...(truncated)")


def main():
    context = build_context()

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }

    print(json.dumps(output))


if __name__ == "__main__":
    main()
