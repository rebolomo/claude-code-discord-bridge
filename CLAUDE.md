# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# claude-code-discord-bridge (ccdb)

Discord frontend for Claude Code CLI. **This is a framework (OSS library), not a personal bot.**

## Framework vs Instance

- **claude-code-discord-bridge** (this repo) = reusable OSS framework. No personal config, no secrets, no server-specific logic.
- Personal instances (e.g. EbiBot) use the custom Cog loader (`CUSTOM_COGS_DIR` / `--cogs-dir`) to add their own Cogs. See `examples/ebibot/` for the reference implementation.
- When adding features: if it's useful to anyone → add here. If it's personal workflow → add as a custom Cog.

### Zero-Config Principle (Critical)

**Consumers must get new features by updating the package alone — no code changes required.**

- New features should be enabled by default (auto-discovery, sensible defaults)
- New constructor parameters must have backward-compatible defaults (`= None`)
- If a feature requires consumers to wire something up, the design is wrong — fix it in ccdb

## Architecture

- **Python 3.10+** with discord.py v2
- **Cog pattern** for modular features
- **Repository pattern** for data access (SQLite via aiosqlite)
- **asyncio.subprocess** for Claude CLI invocation (never shell=True)

### Key Design Decisions

1. **CLI spawn, not API**: We invoke `claude -p --output-format stream-json` as a subprocess. This gives all Claude Code features (CLAUDE.md, skills, tools, memory) for free.
2. **Thread = Session**: Each Discord thread maps 1:1 to a Claude Code session ID. Replies continue the same session via `--resume`.
3. **Emoji reactions for status**: Non-intrusive progress indication. Debounced to avoid Discord rate limits.
4. **Fence-aware chunking**: Never split Discord messages inside a code block.
5. **REST API as the control plane**: Claude Code communicates back via `CCDB_API_URL` (env var), not stdout markers. Makes the interface explicit, testable, and usable by external systems.
6. **SQLite-backed dynamic scheduler**: Tasks stored in `scheduled_tasks` table, fired by a 30-second master loop. Tasks are registered at runtime via REST API — no code changes needed.
7. **Claude handles "what", ccdb handles "when"**: ccdb only manages the schedule. All domain logic lives in the Claude prompt.
8. **System-prompt injection**: The AI Lounge context is injected via `--append-system-prompt` (ephemeral, not accumulated in history) so long sessions never hit "Prompt is too long".

## Development

### Setup

```bash
git clone https://github.com/ebibibi/claude-code-discord-bridge.git
cd claude-code-discord-bridge
uv sync --dev
make setup    # configure .githooks/pre-commit
```

### Run a Single Test

```bash
uv run pytest tests/test_parser.py::test_parse_system_message -v
```

### Running Tests

```bash
uv run pytest tests/ -v --cov=claude_discord
```

All tests must pass before submitting a PR. CI runs on Python 3.10, 3.11, and 3.12.

### Linting & Formatting

```bash
make check    # format check + lint (same as CI)
make format   # auto-format
```

CI enforces both. Use `make check` before pushing.

### Running (standalone)

```bash
cp .env.example .env
# Edit .env with your Discord bot token and channel ID
uv run python -m claude_discord.main
```

### Dev Workflow (worktree + Discord testing)

The EbiBot deployment uses a **sys.meta_path hook** to redirect `import claude_discord` to a worktree. Steps:

```bash
# 1. Create worktree
git worktree add ../wt-my-feature -b feat/my-feature

# 2. Implement and unit test
cd ../wt-my-feature && uv run pytest tests/ -v

# 3. Enable dev mode (loads claude_discord from worktree, not main)
make dev-on    # writes path to ~/.ccdb-dev-worktree, restarts bot

# 4. Test in Discord until user says OK

# 5. Disable dev mode, push branch, create PR
make dev-off
make pr
```

**How the hook works:** `scripts/pre-start.sh` places `_ccdb_dev_hook.py` + `_ccdb_dev_hook.pth` in the venv. On import, it inserts a `sys.meta_path` finder that reads `~/.ccdb-dev-worktree` and redirects `claude_discord` imports to that path. This beats `python -m`'s CWD-first resolution.

For deployment automation (`git pull`, `uv sync`, rollback on failure), see `scripts/pre-start.sh` — don't assume manual restarts are needed.

## Code Conventions

### Style

- **Formatter/Linter**: ruff (config in `pyproject.toml`)
- **Type hints**: Required on all function signatures
- **Python**: 3.10+ — use `from __future__ import annotations` in every file
- **Line length**: 100 characters max

### Error Handling

- Use `contextlib.suppress(discord.HTTPException)` for Discord API calls that may fail (reactions, message edits)
- Never silently swallow errors in business logic — log them
- CLI subprocess errors should yield a `StreamEvent` with `error` field, not raise exceptions

### Security (Mandatory — Auto-Enforced)

This project runs arbitrary Claude Code sessions. Security is non-negotiable.

**Before every commit**, run the security audit:

- **Always `create_subprocess_exec`**: Never use `shell=True`. Prompt is a direct argument, not shell-interpolated.
- **`--` separator**: Always use `--` before the prompt argument to prevent flag injection.
- **Session ID validation**: Strict regex `^[a-f0-9\-]+$` before passing to `--resume`.
- **Skill name validation**: Strict regex `^[\w-]+$` before passing to Claude.
- **Environment stripping**: `DISCORD_BOT_TOKEN` and other secrets are removed from the subprocess env.
- **No `dangerously_skip_permissions` by default**.

If you modify `runner.py`, `_run_helper.py`, or any Cog, the security audit is mandatory before committing.

### Testing (TDD Enforced)

**All new features and bug fixes MUST follow TDD: write tests FIRST, then implement.**

1. **RED**: Write a failing test → `uv run pytest tests/test_xxx.py -v` → confirm it FAILS
2. **GREEN**: Write minimal code to pass → confirm it PASSES
3. **REFACTOR**: Clean up, keeping tests green
4. **VERIFY**: `make check && uv run pytest tests/ -v --cov=claude_discord`

## Project Structure

```
claude_discord/
  __init__.py            # Public API exports
  cli.py                 # CLI entry point (ccdb setup/start commands)
  main.py                # Standalone entry point
  setup.py               # setup_bridge() — one-call Cog wiring
  cog_loader.py          # Dynamic custom Cog loader
  bot.py                 # Discord Bot class
  protocols.py           # Shared protocols (DrainAware)
  concurrency.py         # Concurrency notice + active session registry
  lounge.py              # AI Lounge prompt builder
  session_sync.py        # CLI session discovery and import
  worktree.py            # WorktreeManager
  cogs/
    claude_chat.py       # Thread creation and message handling
    skill_command.py     # /skill slash command
    session_manage.py    # /sessions, /sync-sessions, /resume-info
    session_sync.py      # Thread/message logic for sync-sessions
    prompt_builder.py    # build_prompt_and_images() — pure function
    webhook_trigger.py   # Webhook → Claude task execution
    auto_upgrade.py      # Webhook → package upgrade + drain-aware restart
    scheduler.py         # SQLite-backed periodic task executor (30s master loop)
    event_processor.py   # EventProcessor — stream-json event state machine
    run_config.py        # RunConfig dataclass — all CLI execution params
    _run_helper.py       # run_claude_in_thread() — shared orchestration
  claude/
    runner.py            # Claude CLI subprocess manager
    parser.py            # stream-json event parser
    types.py             # Type definitions for SDK messages
  database/
    models.py            # SQLite schema
    repository.py        # Session CRUD operations
    task_repo.py         # Scheduled task CRUD
    ask_repo.py          # Pending AskUserQuestion CRUD
    notification_repo.py # Scheduled notification CRUD
    lounge_repo.py       # AI Lounge message CRUD
    resume_repo.py       # Startup resume CRUD
    settings_repo.py     # Per-guild settings
    inbox_repo.py        # Thread inbox CRUD
    expandable_repo.py   # Expandable content CRUD
  discord_ui/
    status.py            # Emoji reaction status manager (debounced)
    statusline.py        # StatusLine display from ~/.claude/settings.json
    chunker.py           # Fence- and table-aware message splitting
    embeds.py            # Discord embed builders
    views.py             # Stop button, ToolSelectView, shared UI components
    ask_bus.py           # Event bus for AskUserQuestion communication
    ask_view.py          # Buttons/Select Menus for AskUserQuestion
    ask_handler.py       # collect_ask_answers() — AskUserQuestion UI + DB lifecycle
    streaming_manager.py # Debounced in-place message edits
    tool_timer.py        # LiveToolTimer — elapsed time for long-running tools
    thread_dashboard.py  # Live pinned embed showing session states
    plan_view.py         # Approve/Cancel for Plan Mode (ExitPlanMode)
    permission_view.py   # Allow/Deny for tool permission requests
    elicitation_view.py  # Discord UI for MCP elicitation
    file_sender.py       # File delivery via .ccdb-attachments
    inbox_classifier.py  # classify() — claude -p call to label session final state
    thread_renamer.py    # suggest_title() — background claude -p call for auto naming
  ext/
    api_server.py        # REST API server (optional, requires aiohttp)
  utils/
    logger.py            # Logging setup
tests/                   # pytest test suite
examples/
  ebibot/
    cogs/                # ReminderCog, WatchdogCog, AutoUpgradeCog, DocsSyncCog
```

### Adding a New Cog

1. Create `claude_discord/cogs/your_cog.py`
2. If it runs Claude CLI, use `_run_helper.run_claude_in_thread()` — don't duplicate the streaming logic
3. Export from `claude_discord/cogs/__init__.py` and `claude_discord/__init__.py`
4. Write tests in `tests/test_your_cog.py`

### Custom Cog Protocol

Custom Cogs loaded via `CUSTOM_COGS_DIR` / `--cogs-dir` must expose:

```python
async def setup(bot, runner, components):
    await bot.add_cog(MyCog(bot))
```

Rules: files prefixed with `_` are skipped; one Cog's failure is logged and skipped — never blocks others; `examples/ebibot/cogs/` is the reference implementation.

## Git & PR Workflow

- **Branch from `main`**: `feature/description`, `fix/description`
- **CI must pass**: All 3 Python versions × (ruff check + ruff format + pytest)
- **No direct push to main**: Always create a PR
- **Commit style**: `<type>: <description>` — types: feat, fix, refactor, docs, test, chore, security

## Commands & Skills

| Command | Usage |
|---------|-------|
| `/verify` | Full verification pipeline |
| `/new-cog <name>` | Scaffold a new Cog |

| Skill | Purpose |
|-------|---------|
| `tdd` | Enforced test-driven development |
| `verify` | Pre-commit quality gate |
| `add-cog` | Step-by-step guide to scaffold a new Cog |
| `security-audit` | Security checklist for subprocess/injection threats |
| `python-quality` | Python patterns and project conventions |
| `test-guide` | Testing patterns, mocking Discord objects |

**PostToolUse hook** (`.claude/settings.json`): Auto-formats `.py` files with ruff after Edit/Write.

## What Does NOT Belong Here

- Personal bot configuration (tokens, channel IDs, user IDs)
- Server-specific Cogs or workflows
- Direct Anthropic API calls (we use Claude Code CLI, not the API)
- Heavy dependencies that most users won't need