# Changelog

## 2026-10-02

Fixes a SessionEnd recursion loop, makes `compile.py` work on large wikis and report failures honestly, and stops compiler runs from filling the disk with transcripts. Verified against SDK `claude-agent-sdk` 0.1.56 (bundled Claude Code 2.1.92).

### Fixed

- **Recursion loop.** Every `query()` call in `llm_client.py` and `flush.py` starts a full Claude Code session that loaded user, project and local settings, so a global `SessionEnd` hook that runs `compile.py` fired again when the service session ended, indefinitely. Observed: 4129 leftover compile sessions (4.4 GB) and 358 flush sessions in one project.
  - Service sessions now run with `--strict-mcp-config`, `--setting-sources ""` and `CLAUDE_INVOKED_BY` set, so no hooks and no MCP servers load. Measured fixed prompt overhead per session: 57 812 to 25 699 tokens.
  - `setting_sources=[]` and `mcp_servers={}` do not work in SDK 0.1.56 (the SDK skips falsy values), so the flags go through `extra_args`.
  - `compile.py` and `lint.py` set `CLAUDE_INVOKED_BY` before any import; `hooks/session-start.py` exits when it is set.
- **`Prompt is too long`.** `compile.py` put the full text of every article into the prompt (88% of 608K characters for 102 articles), which overflowed the 200K context of Haiku 4.5. The prompt now has the index, all article paths and the full text of at most 8 related articles (60K characters).
- **Silent failure.** `compile.py` printed `Done` and marked a log as ingested even when the session wrote nothing or stopped early. Success now requires a new `compile` header for that log in `knowledge/log.md`.
- **Prompt bug.** Rule 7 ("Maintain bidirectional links") sat inside the log-entry code block, so the agent could copy it into `log.md`. Moved out of the block.
- **Hidden errors.** The SDK reports only "Check stderr output for details" unless a `stderr` callback is registered; `llm_client.py` and `flush.py` now append the last stderr lines to the error.

### Added

- `no-session-persistence` for all service sessions: no transcript file is left behind (checked: file count unchanged after a call).
- `utils.select_related_articles()`: model-free selection of related articles by inverse-document-frequency overlap with the log.
- `utils.snapshot_knowledge()`, `read_log_text()`, `verify_compile()`: result check used by `compile.py`.
- `utils.find_ungrounded_identifiers()`, `build_grounding_corpus()`, `changed_articles()`: after each compile, prints `UNGROUNDED: <article>: <name>` for env vars, `--flags` and file names in the new or changed articles that appear in neither the daily log nor the project's tracked files. Warning only. External variable names such as `OPENAI_API_KEY` can be reported as noise.
- `LLMResult.subtype` and `LLMResult.num_turns`; a session that ends with `is_error` is reported as `LLM_SESSION_ERROR`.
- `scripts/cleanup_sessions.py`: removes leftover service sessions by the first user message (compile, flush, lint, query prompts and test pings), their companion directories and stale `session-flush-*.md` files. Dry run by default, `--apply` deletes; real sessions are never matched.

### Changed

- Compile prompt and `AGENTS.md` schema: grounding rules (state only what the log supports; never invent names, variables, commands, paths, versions, numbers or dates; verify checkable details when the project has code or docs, otherwise omit or mark "(not verified: from the log only)").
- Quotas replaced by "as many as the source supports": 3-7 concepts per log, 3-10 articles per log, 3-5 key points, 2+ paragraphs, 2+ related entries. "Comprehensive" removed from the style rule.
- The compile agent must always append a `log.md` entry, with header `compile | daily/<log>`, even when nothing needed changing.
- `flush.py` prompt: record only what was said or done in the context.

### Known limitations

- A global hook that runs `compile.py` should still check `CLAUDE_INVOKED_BY` itself (and use `flock`); the compiler cannot control hooks outside this repository.
- Related-article selection is lexical. In an A/B run on one log, coverage of old-article updates varied between runs (1 to 6 new articles). The Grep step in the prompt compensates only partly.
- `lint.py` without `--structural-only` still sends the whole wiki to the model (`check_contradictions`); it breaks at about 100 articles. Hooks use `--structural-only`, which skips it.
- Not run end to end after the last prompt change: one real compile on the final prompt is still to be measured for output size and cost.
