"""Path constants and configuration for the personal knowledge base.

This is a vendored copy of coleam00/claude-memory-compiler, relocated so the
tooling lives under .claude/wiki/claude-memory-compiler while the actual wiki
data (daily/, knowledge/) lives at <project root>/.wiki/, matching the
.wiki (data) vs .claude/wiki (runtime/reference) split used across projects.

Override the data root with CLAUDE_WIKI_ROOT (default: .wiki, relative to
project root) and the timezone with CLAUDE_WIKI_TIMEZONE (default: Europe/Moscow).
"""

import os
from pathlib import Path
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────
# Tool directory: <project>/.claude/wiki/claude-memory-compiler
ROOT_DIR = Path(__file__).resolve().parent.parent
# Project root: three levels up from the tool directory
PROJECT_ROOT = ROOT_DIR.parent.parent.parent
WIKI_ROOT = PROJECT_ROOT / os.environ.get("CLAUDE_WIKI_ROOT", ".wiki")

DAILY_DIR = WIKI_ROOT / "daily"
KNOWLEDGE_DIR = WIKI_ROOT / "knowledge"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
CONNECTIONS_DIR = KNOWLEDGE_DIR / "connections"
QA_DIR = KNOWLEDGE_DIR / "qa"
REPORTS_DIR = WIKI_ROOT / "reports"
SCRIPTS_DIR = ROOT_DIR / "scripts"
HOOKS_DIR = ROOT_DIR / "hooks"
AGENTS_FILE = ROOT_DIR / "AGENTS.md"

INDEX_FILE = KNOWLEDGE_DIR / "index.md"
LOG_FILE = KNOWLEDGE_DIR / "log.md"
STATE_FILE = SCRIPTS_DIR / "state.json"

# ── Timezone ───────────────────────────────────────────────────────────
TIMEZONE = os.environ.get("CLAUDE_WIKI_TIMEZONE", "Europe/Moscow")


def now_iso() -> str:
    """Current time in ISO 8601 format."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    """Current date in ISO 8601 format."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
