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
from datetime import datetime
from zoneinfo import ZoneInfo

# ── Paths ──────────────────────────────────────────────────────────────
# Tool directory: <project>/.claude/wiki/claude-memory-compiler
ROOT_DIR = Path(__file__).resolve().parent.parent
# Project root: three levels up from the tool directory
PROJECT_ROOT = ROOT_DIR.parent.parent.parent

_root_env = os.environ.get("CLAUDE_WIKI_ROOT", "")
if _root_env:
    _root_candidate = Path(_root_env).expanduser()
    if not _root_candidate.is_absolute():
        _root_candidate = PROJECT_ROOT / _root_candidate
    WIKI_ROOT = _root_candidate.resolve()
else:
    WIKI_ROOT = (PROJECT_ROOT / ".wiki").resolve()

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
    """Current time in ISO 8601 format, in the configured TIMEZONE."""
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")


def today_iso() -> str:
    """Current date (YYYY-MM-DD) in the configured TIMEZONE."""
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
