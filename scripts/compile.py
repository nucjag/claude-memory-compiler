"""
Compile daily conversation logs into structured knowledge articles.

This is the "LLM compiler" - it reads daily logs (source code) and produces
organized knowledge articles (the executable).

Usage:
    uv run python compile.py                    # compile new/changed logs only
    uv run python compile.py --all              # force recompile everything
    uv run python compile.py --file daily/2026-04-01.md  # compile a specific log
    uv run python compile.py --dry-run          # show what would be compiled
"""

from __future__ import annotations

# Recursion prevention: set before any Claude session can start
import os
os.environ.setdefault("CLAUDE_INVOKED_BY", "memory_compiler")

import argparse
import asyncio
import sys
from pathlib import Path

from config import AGENTS_FILE, CONCEPTS_DIR, CONNECTIONS_DIR, DAILY_DIR, KNOWLEDGE_DIR, PROJECT_DIR, now_iso
from llm_client import run_compile_with_fallback
from utils import (
    enforce_backlinks,
    file_hash,
    list_raw_files,
    list_wiki_articles,
    load_state,
    build_grounding_corpus,
    changed_articles,
    find_ungrounded_identifiers,
    read_log_text,
    read_wiki_index,
    save_state,
    select_related_articles,
    snapshot_knowledge,
    verify_compile,
)

# ── Paths for the LLM to use ──────────────────────────────────────────
ROOT_DIR = PROJECT_DIR


async def compile_daily_log(
    log_path: Path,
    state: dict,
    provider_order: str | None,
    timeout_s: int | None,
    openai_model: str | None,
) -> tuple[float, bool]:
    """Compile a single daily log into knowledge articles.

    Returns the API cost of the compilation.
    """
    log_content = log_path.read_text(encoding="utf-8")
    schema = AGENTS_FILE.read_text(encoding="utf-8")
    wiki_index = read_wiki_index()

    # Send the index, all article paths and only the most related articles in full.
    # Sending every article overflows the model context as the wiki grows.
    related = select_related_articles(log_content)
    related_context = "\n\n".join(
        f"### {p.relative_to(KNOWLEDGE_DIR).as_posix()}\n```markdown\n{p.read_text(encoding='utf-8')}\n```"
        for p in related
    )
    all_paths = "\n".join(f"- {p}" for p in list_wiki_articles())

    timestamp = now_iso()

    prompt = f"""You are a knowledge compiler. Your job is to read a daily conversation log
and extract knowledge into structured wiki articles.

## Schema (AGENTS.md)

{schema}

## Current Wiki Index

{wiki_index}

## Most Related Existing Articles (full text)

{related_context if related_context else "(none selected)"}

## All Existing Article Paths

Full text of the other articles is NOT included. Open any of them with the Read tool before
updating or linking it.

{all_paths if all_paths else "(No existing articles yet)"}

## Daily Log to Compile

**File:** {log_path.name}

{log_content}

## Your Task

Read the daily log above and compile it into wiki articles following the schema exactly.

### Rules:

1. **Extract key concepts** - Identify as many distinct concepts as the log actually supports
   (often 1-4); do not split or pad topics to reach a number
2. **Create concept articles** in `knowledge/concepts/` - One .md file per concept
   - Use the exact article format from AGENTS.md (YAML frontmatter + sections)
   - Include `sources:` in frontmatter pointing to the daily log file
   - Use `[[concepts/slug]]` wikilinks to link to related concepts
   - Write in encyclopedia style - neutral and factual
3. **Create connection articles** in `knowledge/connections/` if this log reveals non-obvious
   relationships between 2+ existing concepts
4. **Update existing articles** if this log adds new information to concepts already in the wiki
   - Read the existing article, add the new information, add the source to frontmatter
   - Before finishing, run Grep over {KNOWLEDGE_DIR} for the key terms of this log (file names,
     identifiers, error codes, component names) and open every article that matches; update it if
     the log adds to it. Do not rely only on the articles included above
5. **Update knowledge/index.md** - Add new entries to the table
   - Each entry: `| [[path/slug]] | One-line summary | source-file | {timestamp[:10]} |`
6. **Append to knowledge/log.md** - ALWAYS add a timestamped entry, even when the log was already
   compiled and nothing needed changing (then write "no new knowledge" in the entry). The entry
   header must contain `compile | daily/{log_path.name}`. Never finish without it:
   ```
   ## [{timestamp}] compile | {log_path.name}
   - Source: daily/{log_path.name}
   - Articles created: [[concepts/x]], [[concepts/y]]
   - Articles updated: [[concepts/z]] (if any)
   ```
7. **Maintain bidirectional links when practical** - when adding `[[target]]` links, prefer also
   adding reciprocal links in the target article's Related Concepts section

### File paths:
- Write concept articles to: {CONCEPTS_DIR}
- Write connection articles to: {CONNECTIONS_DIR}
- Update index at: {KNOWLEDGE_DIR / 'index.md'}
- Append log at: {KNOWLEDGE_DIR / 'log.md'}

### Grounding rules (highest priority):
- State only what the daily log supports. Never invent names, environment variables, commands,
  flags, file paths, versions, numbers, dates, timelines or metrics to fill a section.
- If a detail is checkable (env var, command, path, flag) and the project has code, config or
  docs, verify it with Grep/Read before writing it (a few checks at most); otherwise omit it or
  mark it "(not verified: from the log only)".
- For external tools, state only behavior the log or the tool's own docs support.
- Shorter is better than padded: a section may be one bullet or one short paragraph.

### Quality standards:
- Every article must have complete YAML frontmatter
- Link to related existing articles via [[wikilinks]] (at least 2 when related articles exist)
- Prefer reciprocal related-concept links for new cross-article references
- Key Points: up to 5 bullets, only as many as the log supports
- Details: as many paragraphs as needed, no filler
- Related Concepts: the related articles that actually exist
- Sources section should cite the daily log with specific claims extracted
"""

    before = snapshot_knowledge()
    before_log = read_log_text()
    result = await run_compile_with_fallback(
        prompt=prompt,
        cwd=ROOT_DIR,
        root_dir=ROOT_DIR,
        provider_order=provider_order,
        timeout_s=timeout_s,
        openai_model=openai_model,
    )
    if not result.ok:
        print(f"  Error ({result.error_type}): {result.error}")
        return 0.0, False
    print(f"  Provider: {result.provider}")
    if result.cost_usd > 0:
        print(f"  Cost: ${result.cost_usd:.4f}")

    verified, reason = verify_compile(before, before_log, log_path.name)
    if not verified:
        print(f"  Error (COMPILE_INCOMPLETE): {reason}; session {result.subtype or 'unknown'}, {result.num_turns} turns")
        return result.cost_usd, False
    print(f"  Verified: {reason}")

    ungrounded = find_ungrounded_identifiers(changed_articles(before), build_grounding_corpus(log_content))
    for article, identifier in ungrounded:
        print(f"  UNGROUNDED: {article}: `{identifier}` is in neither the log nor the project files")

    # Update state
    rel_path = log_path.name
    state.setdefault("ingested", {})[rel_path] = {
        "hash": file_hash(log_path),
        "compiled_at": now_iso(),
        "cost_usd": result.cost_usd,
        "provider": result.provider,
    }
    state["total_cost"] = state.get("total_cost", 0.0) + result.cost_usd
    save_state(state)

    return result.cost_usd, True


def main():
    parser = argparse.ArgumentParser(description="Compile daily logs into knowledge articles")
    parser.add_argument("--all", action="store_true", help="Force recompile all logs")
    parser.add_argument("--file", type=str, help="Compile a specific daily log file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be compiled")
    parser.add_argument("--provider-order", type=str, help="LLM providers order, e.g. claude,openai,local")
    parser.add_argument("--openai-model", type=str, help="OpenAI model for fallback, default gpt-5.4")
    parser.add_argument("--timeout", type=int, help="Per-provider timeout in seconds")
    args = parser.parse_args()

    state = load_state()

    # Determine which files to compile
    if args.file:
        target = Path(args.file)
        if not target.is_absolute():
            target = DAILY_DIR / target.name
        if not target.exists():
            # Try resolving relative to project root
            target = ROOT_DIR / args.file
        if not target.exists():
            print(f"Error: {args.file} not found")
            sys.exit(1)
        to_compile = [target]
    else:
        all_logs = list_raw_files()
        if args.all:
            to_compile = all_logs
        else:
            to_compile = []
            for log_path in all_logs:
                rel = log_path.name
                prev = state.get("ingested", {}).get(rel, {})
                if not prev or prev.get("hash") != file_hash(log_path):
                    to_compile.append(log_path)

    if not to_compile:
        print("Nothing to compile - all daily logs are up to date.")
        return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Files to compile ({len(to_compile)}):")
    for f in to_compile:
        print(f"  - {f.name}")

    if args.dry_run:
        return

    # Compile each file sequentially
    total_cost = 0.0
    failed_files: list[str] = []
    succeeded = 0
    for i, log_path in enumerate(to_compile, 1):
        print(f"\n[{i}/{len(to_compile)}] Compiling {log_path.name}...")
        cost, ok = asyncio.run(
            compile_daily_log(
                log_path,
                state,
                provider_order=args.provider_order,
                timeout_s=args.timeout,
                openai_model=args.openai_model,
            )
        )
        total_cost += cost
        if not ok:
            failed_files.append(log_path.name)
            print("  Failed.")
        else:
            succeeded += 1
            print("  Done.")

    if succeeded > 0:
        updated_files = enforce_backlinks()
        print(f"\nBacklink enforcement complete. Updated {updated_files} article(s).")

    articles = list_wiki_articles()
    print(f"\nCompilation complete. Total cost: ${total_cost:.2f}")
    print(f"Knowledge base: {len(articles)} articles")
    if failed_files:
        print(f"Failed files: {', '.join(failed_files)}")
        sys.exit(2)


if __name__ == "__main__":
    main()
