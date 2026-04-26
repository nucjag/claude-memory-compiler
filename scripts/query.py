"""
Query the knowledge base using index-guided retrieval (no RAG).

The LLM reads the index, picks relevant articles, and synthesizes an answer.
No vector database, no embeddings, no chunking - just structured markdown
and an index the LLM can reason over.

Usage:
    uv run python query.py "How should I handle auth redirects?"
    uv run python query.py "What patterns do I use for API design?" --file-back
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from config import KNOWLEDGE_DIR, PROJECT_DIR, QA_DIR, now_iso
from utils import (
    extract_wikilinks,
    list_wiki_articles,
    load_state,
    read_wiki_index,
    save_state,
    wiki_article_exists,
)

ROOT_DIR = PROJECT_DIR


def _article_context(links: list[str]) -> str:
    parts: list[str] = []
    for link in links:
        path = KNOWLEDGE_DIR / f"{link}.md"
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        parts.append(f"## {link}\n\n{content}")
    return "\n\n---\n\n".join(parts)


def _read_current_story() -> str:
    context_path = PROJECT_DIR / ".sdd" / "context.md"
    if not context_path.exists():
        return "unknown"
    text = context_path.read_text(encoding="utf-8")
    match = re.search(r"^- current_story:\s*`?([^`\n]+)`?\s*$", text, flags=re.MULTILINE)
    if not match:
        return "unknown"
    story = match.group(1).strip()
    return story if story and story.upper() != "TBD" else "unknown"


def _append_query_marker(selected_count: int, total_count: int) -> None:
    log_path = PROJECT_DIR / ".sdd" / "token-usage.log"
    story = _read_current_story()
    marker = f"WIKI_QUERY story={story} selected={selected_count} total={total_count}\n"

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(marker)
    except OSError as exc:
        print(
            f"[query] warning: failed to write WIKI_QUERY marker to {log_path}: {exc}",
            file=sys.stderr,
        )


async def _select_relevant_links(question: str, index_content: str) -> list[str]:
    """Pass 1: select relevant article links from the index."""
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    prompt = f"""You are selecting wiki articles for a question.

Return only relevant wikilinks from the index, one per line, max 10 lines.
Output format example:
[[concepts/example]]
[[connections/another]]

If nothing is relevant, output exactly: NONE

## Index

{index_content}

## Question

{question}
"""

    response = ""
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(ROOT_DIR),
            allowed_tools=[],
            max_turns=2,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response += block.text

    links = []
    seen = set()
    for link in extract_wikilinks(response):
        if link in seen:
            continue
        if wiki_article_exists(link):
            links.append(link)
            seen.add(link)
        if len(links) >= 10:
            break
    return links


async def run_query(question: str, file_back: bool = False) -> tuple[str, int, int]:
    """Two-pass query: select by index, then synthesize from selected articles."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    index_content = read_wiki_index()
    total_articles = len(list_wiki_articles())
    selected_links = await _select_relevant_links(question, index_content)

    selected_content = _article_context(selected_links)
    selected_count = len(selected_links)

    tools = []
    if file_back:
        tools.extend(["Write", "Edit"])

    file_back_instructions = ""
    if file_back:
        timestamp = now_iso()
        file_back_instructions = f"""

## File Back Instructions

After answering, do the following:
1. Create a Q&A article at {QA_DIR}/ with the filename being a slugified version
   of the question (e.g., knowledge/qa/how-to-handle-auth-redirects.md)
2. Use the Q&A article format from the schema (frontmatter with title, question,
   consulted articles, filed date)
3. Update {KNOWLEDGE_DIR / 'index.md'} with a new row for this Q&A article
4. Append to {KNOWLEDGE_DIR / 'log.md'}:
   ## [{timestamp}] query (filed) | question summary
   - Question: {question}
   - Consulted: [[list of articles read]]
   - Filed to: [[qa/article-name]]
"""

    prompt = f"""You are a knowledge base query engine. Answer the user's question using
the selected wiki articles below.

## How to Answer

1. Use only selected articles as primary evidence
2. Synthesize a clear answer
3. Cite sources using [[wikilinks]]
4. If selected articles are insufficient, explicitly say what's missing

## Selected Articles

{selected_content if selected_content else "(No selected articles)"}

## Question

{question}
{file_back_instructions}"""

    answer = ""
    cost = 0.0

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                cwd=str(ROOT_DIR),
                system_prompt={"type": "preset", "preset": "claude_code"},
                allowed_tools=tools,
                permission_mode="acceptEdits",
                max_turns=15,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        answer += block.text
            elif isinstance(message, ResultMessage):
                cost = message.total_cost_usd or 0.0
    except Exception as e:
        answer = f"Error querying knowledge base: {e}"

    # Update state
    state = load_state()
    state["query_count"] = state.get("query_count", 0) + 1
    state["total_cost"] = state.get("total_cost", 0.0) + cost
    state["last_query_selection"] = {
        "selected_articles": selected_count,
        "total_articles": total_articles,
        "question": question,
    }
    save_state(state)
    _append_query_marker(selected_count, total_articles)

    return answer, selected_count, total_articles


def main():
    parser = argparse.ArgumentParser(description="Query the personal knowledge base")
    parser.add_argument("question", help="The question to ask")
    parser.add_argument(
        "--file-back",
        action="store_true",
        help="File the answer back into the knowledge base as a Q&A article",
    )
    args = parser.parse_args()

    print(f"Question: {args.question}")
    print(f"File back: {'yes' if args.file_back else 'no'}")
    print("-" * 60)

    answer, selected_count, total_articles = asyncio.run(
        run_query(args.question, file_back=args.file_back)
    )
    print(f"Selection pass: {selected_count}/{total_articles} article(s)")
    print("-" * 60)
    print(answer)

    if args.file_back:
        print("\n" + "-" * 60)
        qa_count = len(list(QA_DIR.glob("*.md"))) if QA_DIR.exists() else 0
        print(f"Answer filed to knowledge/qa/ ({qa_count} Q&A articles total)")


if __name__ == "__main__":
    main()
