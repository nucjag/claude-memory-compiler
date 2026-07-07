"""Shared utilities for the personal knowledge base."""

import hashlib
import json
import re
from pathlib import Path

from config import (
    CONCEPTS_DIR,
    CONNECTIONS_DIR,
    DAILY_DIR,
    INDEX_FILE,
    KNOWLEDGE_DIR,
    LOG_FILE,
    QA_DIR,
    STATE_FILE,
)


# ── State management ──────────────────────────────────────────────────

def load_state() -> dict:
    """Load persistent state from state.json."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"ingested": {}, "query_count": 0, "last_lint": None, "total_cost": 0.0}


def save_state(state: dict) -> None:
    """Save state to state.json."""
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── File hashing ──────────────────────────────────────────────────────

def file_hash(path: Path) -> str:
    """SHA-256 hash of a file (first 16 hex chars)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# ── Slug / naming ─────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# ── Wikilink helpers ──────────────────────────────────────────────────

def extract_wikilinks(content: str) -> list[str]:
    """Extract all [[wikilinks]] from markdown content."""
    return re.findall(r"\[\[([^\]]+)\]\]", content)


def wiki_article_exists(link: str) -> bool:
    """Check if a wikilinked article exists on disk."""
    path = KNOWLEDGE_DIR / f"{link}.md"
    return path.exists()


# ── Wiki content helpers ──────────────────────────────────────────────

def read_wiki_index() -> str:
    """Read the knowledge base index file."""
    if INDEX_FILE.exists():
        return INDEX_FILE.read_text(encoding="utf-8")
    return "# Knowledge Base Index\n\n| Article | Summary | Compiled From | Updated |\n|---------|---------|---------------|---------|"


def read_all_wiki_content() -> str:
    """Read index + all wiki articles into a single string for context."""
    parts = [f"## INDEX\n\n{read_wiki_index()}"]

    for subdir in [CONCEPTS_DIR, CONNECTIONS_DIR, QA_DIR]:
        if not subdir.exists():
            continue
        for md_file in sorted(subdir.glob("*.md")):
            rel = md_file.relative_to(KNOWLEDGE_DIR)
            content = md_file.read_text(encoding="utf-8")
            parts.append(f"## {rel}\n\n{content}")

    return "\n\n---\n\n".join(parts)


def list_wiki_articles() -> list[Path]:
    """List all wiki article files."""
    articles = []
    for subdir in [CONCEPTS_DIR, CONNECTIONS_DIR, QA_DIR]:
        if subdir.exists():
            articles.extend(sorted(subdir.glob("*.md")))
    return articles


def list_raw_files() -> list[Path]:
    """List all daily log files."""
    if not DAILY_DIR.exists():
        return []
    return sorted(DAILY_DIR.glob("*.md"))


# ── Index helpers ─────────────────────────────────────────────────────

def count_inbound_links(target: str, exclude_file: Path | None = None) -> int:
    """Count how many wiki articles link to a given target."""
    count = 0
    for article in list_wiki_articles():
        if article == exclude_file:
            continue
        content = article.read_text(encoding="utf-8")
        if f"[[{target}]]" in content:
            count += 1
    return count


def get_article_word_count(path: Path) -> int:
    """Count words in an article, excluding YAML frontmatter."""
    content = path.read_text(encoding="utf-8")
    # Strip frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:]
    return len(content.split())


def build_index_entry(rel_path: str, summary: str, sources: str, updated: str) -> str:
    """Build a single index table row."""
    link = rel_path.replace(".md", "")
    return f"| [[{link}]] | {summary} | {sources} | {updated} |"


# ── Backlink enforcement ───────────────────────────────────────────────

def list_all_knowledge_articles() -> list[Path]:
    """List all markdown knowledge articles recursively, excluding index/log."""
    if not KNOWLEDGE_DIR.exists():
        return []
    articles = []
    for md_file in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        rel = md_file.relative_to(KNOWLEDGE_DIR)
        if rel.as_posix() in {"index.md", "log.md"}:
            continue
        articles.append(md_file)
    return articles


def _article_link_from_path(path: Path) -> str:
    """Convert knowledge article path to wikilink target path."""
    rel = path.relative_to(KNOWLEDGE_DIR)
    return str(rel).replace(".md", "").replace("\\", "/")


def _insert_related_concept(content: str, backlink: str) -> str:
    """Insert a backlink into Related Concepts section, creating it when missing."""
    section_header = "## Related Concepts"
    backlink_line = f"- [[{backlink}]]"

    if backlink_line in content:
        return content

    if section_header in content:
        lines = content.splitlines()
        start_idx = None
        for i, line in enumerate(lines):
            if line.strip() == section_header:
                start_idx = i
                break

        if start_idx is None:
            return content

        end_idx = len(lines)
        for j in range(start_idx + 1, len(lines)):
            if lines[j].startswith("## "):
                end_idx = j
                break

        existing_items = set()
        for line in lines[start_idx + 1:end_idx]:
            s = line.strip()
            if s.startswith("- [[") and s.endswith("]]"):
                existing_items.add(s[2:])

        backlink_token = f"[[{backlink}]]"
        if backlink_token not in existing_items:
            insert_at = end_idx
            for j in range(end_idx - 1, start_idx, -1):
                if lines[j].strip():
                    insert_at = j + 1
                    break
            lines.insert(insert_at, backlink_line)

        return "\n".join(lines) + ("\n" if content.endswith("\n") else "")

    trimmed = content.rstrip()
    if trimmed:
        return f"{trimmed}\n\n{section_header}\n{backlink_line}\n"
    return f"{section_header}\n{backlink_line}\n"


def enforce_backlinks() -> int:
    """Ensure A->B links have B->A backlinks in Related Concepts.

    Returns number of modified files.
    """
    articles = list_all_knowledge_articles()
    if not articles:
        return 0

    source_links: dict[Path, str] = {article: _article_link_from_path(article) for article in articles}
    existing_links = set(source_links.values())
    contents: dict[Path, str] = {article: article.read_text(encoding="utf-8") for article in articles}
    pending_backlinks: dict[Path, set[str]] = {}

    for source_path, source_content in contents.items():
        source_link = source_links[source_path]
        for target_link in extract_wikilinks(source_content):
            if target_link.startswith("daily/"):
                continue
            if target_link not in existing_links:
                continue
            target_path = KNOWLEDGE_DIR / f"{target_link}.md"
            if target_path == source_path:
                continue
            target_content = contents.get(target_path)
            if target_content is None:
                continue
            if f"[[{source_link}]]" in target_content:
                continue
            pending_backlinks.setdefault(target_path, set()).add(source_link)

    modified = 0
    for target_path, backlinks in pending_backlinks.items():
        original = contents[target_path]
        updated = original
        for source_link in sorted(backlinks):
            updated = _insert_related_concept(updated, source_link)
        if updated != original:
            target_path.write_text(updated, encoding="utf-8")
            modified += 1

    return modified
