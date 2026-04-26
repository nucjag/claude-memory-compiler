"""Path constants and configuration for the personal knowledge base."""

import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parents[4]
DEFAULT_ROOT = PROJECT_DIR / ".wiki"
root_env = os.environ.get("CLAUDE_WIKI_ROOT", "")
if root_env:
    root_candidate = Path(root_env).expanduser()
    if not root_candidate.is_absolute():
        root_candidate = PROJECT_DIR / root_candidate
    ROOT_DIR = root_candidate.resolve()
else:
    ROOT_DIR = DEFAULT_ROOT.resolve()
COMPILER_DIR = Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT_DIR / "daily"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
CONNECTIONS_DIR = KNOWLEDGE_DIR / "connections"
QA_DIR = KNOWLEDGE_DIR / "qa"
REPORTS_DIR = ROOT_DIR / "reports"
SCRIPTS_DIR = COMPILER_DIR / "scripts"
HOOKS_DIR = COMPILER_DIR / "hooks"
AGENTS_FILE = COMPILER_DIR / "AGENTS.md"

INDEX_FILE = KNOWLEDGE_DIR / "index.md"
LOG_FILE = KNOWLEDGE_DIR / "log.md"
STATE_FILE = SCRIPTS_DIR / "state.json"

# ── Timezone ───────────────────────────────────────────────────────────
TIMEZONE = os.environ.get("CLAUDE_WIKI_TIMEZONE", "Europe/Moscow")


def now_iso() -> str:
    """Current time in ISO 8601 format."""
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")


def today_iso() -> str:
    """Current date in ISO 8601 format."""
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
