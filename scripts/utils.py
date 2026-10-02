"""Shared utilities for the personal knowledge base."""

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from config import (
    CONCEPTS_DIR,
    CONNECTIONS_DIR,
    DAILY_DIR,
    INDEX_FILE,
    KNOWLEDGE_DIR,
    PROJECT_DIR,
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


# ── Compile support: related-article selection and result verification ─

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{3,}|[А-Яа-яЁё]{5,}")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def select_related_articles(log_text: str, n: int = 8, max_chars: int = 60_000) -> list[Path]:
    """Pick the articles that share the most distinctive words with a daily log.

    Deterministic and model-free: words are weighted by inverse document frequency
    over all articles, words present in more than 30% of articles are ignored.
    """
    articles = list_wiki_articles()
    if not articles:
        return []

    texts = {a: a.read_text(encoding="utf-8") for a in articles}
    article_tokens = {a: _tokens(t) | _tokens(a.stem.replace("-", " ")) for a, t in texts.items()}
    doc_freq: dict[str, int] = {}
    for toks in article_tokens.values():
        for t in toks:
            doc_freq[t] = doc_freq.get(t, 0) + 1

    total = len(articles)
    log_tokens = _tokens(log_text)
    scored: list[tuple[float, Path]] = []
    for article, toks in article_tokens.items():
        score = 0.0
        for t in log_tokens & toks:
            df = doc_freq[t]
            if df > total * 0.3:
                continue
            score += math.log(total / df)
        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: (-x[0], str(x[1])))
    selected: list[Path] = []
    used = 0
    for _, article in scored:
        size = len(texts[article])
        if selected and used + size > max_chars:
            continue
        selected.append(article)
        used += size
        if len(selected) >= n:
            break
    return selected


def snapshot_knowledge() -> dict[str, str]:
    """Hash of index.md, log.md and every article, keyed by path relative to knowledge/."""
    snap: dict[str, str] = {}
    for md_file in KNOWLEDGE_DIR.rglob("*.md") if KNOWLEDGE_DIR.exists() else []:
        snap[md_file.relative_to(KNOWLEDGE_DIR).as_posix()] = file_hash(md_file)
    return snap


def read_log_text() -> str:
    log_file = KNOWLEDGE_DIR / "log.md"
    return log_file.read_text(encoding="utf-8") if log_file.exists() else ""


def verify_compile(before: dict[str, str], before_log: str, log_name: str) -> tuple[bool, str]:
    """Check that a compile session did its job, not just that it ended without error.

    Success needs a new compile header in log.md that names the daily log (the agent
    varies the exact wording, for example "compile | daily/2026-09-28.md (...)"). Changed
    articles are not required: a log that was already compiled legitimately changes nothing,
    and the prompt makes the agent say so in the entry. A missing entry means a silent
    no-op or a partial run.
    """
    after = snapshot_knowledge()
    known = set(before_log.splitlines())
    stem = log_name.removesuffix(".md")
    headers = [
        line for line in read_log_text().splitlines()
        if line not in known and line.startswith("## [") and "compile" in line and stem in line
    ]
    if not headers:
        return False, f"log.md has no new compile entry for {stem}"
    changed = [k for k, v in after.items() if k != "log.md" and before.get(k) != v]
    return True, f"log entry present, {len(changed)} article/index file(s) created or changed"


# ── Grounding check: identifiers in articles must exist in the sources ─

_BACKTICK_RE = re.compile(r"`([^`\n]{3,80})`")
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_UPPER_SNAKE_RE = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9]*_[A-Z0-9_]+$")
_FLAG_RE = re.compile(r"^--[a-z][a-z0-9-]+$")
_FILE_RE = re.compile(r"^[\w./-]*\w\.(?:py|ts|tsx|js|json|ya?ml|toml|md|sh|sql|env\w*|example)$")
_CODE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".sh", ".sql", ".md", ".txt", ".html", ".css", ".conf", ".example",
}
_CORPUS_MAX_BYTES = 15_000_000


def _checkable_identifier(token: str) -> bool:
    """Env vars, long flags and file names: things that either exist or do not."""
    return bool(_ENV_VAR_RE.match(token) or _FLAG_RE.match(token) or _FILE_RE.match(token))


def build_grounding_corpus(source_text: str) -> str:
    """The daily log plus the project's own tracked text files (never the wiki itself).

    A project without code simply yields the log alone, so the check still works there.
    """
    parts = [source_text]
    try:
        listing = subprocess.run(
            ["git", "-C", str(PROJECT_DIR), "ls-files", "-z"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.split("\0")
    except (subprocess.SubprocessError, OSError):
        return source_text

    total = 0
    for rel in listing:
        if not rel or rel.startswith((".wiki/", ".claude/", "node_modules/", ".venv/")):
            continue
        path = PROJECT_DIR / rel
        name = path.name
        if path.suffix.lower() not in _CODE_SUFFIXES and not name.startswith((".env", "Dockerfile")):
            continue
        try:
            size = path.stat().st_size
            if size > 1_000_000 or total + size > _CORPUS_MAX_BYTES:
                continue
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
            total += size
        except OSError:
            continue
    return "\n".join(parts)


def changed_articles(before: dict[str, str]) -> list[Path]:
    """Articles created or changed since the snapshot, excluding index.md and log.md."""
    after = snapshot_knowledge()
    return [
        KNOWLEDGE_DIR / rel
        for rel, digest in sorted(after.items())
        if rel not in ("index.md", "log.md") and before.get(rel) != digest
    ]


def find_ungrounded_identifiers(article_paths: list[Path], corpus: str) -> list[tuple[str, str]]:
    """Backticked env vars, flags and file names in the articles that appear nowhere in the corpus."""
    found: list[tuple[str, str]] = []
    for path in article_paths:
        text = path.read_text(encoding="utf-8")
        seen: set[str] = set()
        tokens = [t.strip().split("=", 1)[0] for t in _BACKTICK_RE.findall(text)]
        tokens += _UPPER_SNAKE_RE.findall("\n".join(_FENCE_RE.findall(text)))
        for token in tokens:
            if token in seen or not _checkable_identifier(token):
                continue
            seen.add(token)
            if token not in corpus:
                found.append((path.relative_to(KNOWLEDGE_DIR).as_posix(), token))
    return found
