# AGENTS.md — notes for future agents working on planka-checker

## What this project is

MCP server + CLI + library for Planka board activity reports.
- **Library API**: `from planka_checker import PlankaSettings, PlankaReportGenerator, PlankaReport`
- **MCP server**: `planka-checker-mcp` (stdio) — 6 tools: `get_daily_report`, `get_weekly_report`, `get_overdue_cards`, `get_burning_cards`, `get_forgotten_cards`, `get_full_report`
- **CLI**: `planka-checker {daily,weekly,overdue,burning,forgotten} [--pretty|--summarize]`

## Layout

```
src/planka_checker/
├── __init__.py        # public re-exports
├── config.py          # PlankaSettings (pydantic-settings, .env + env vars)
├── planka_models.py   # internal lightweight dataclasses for raw Planka API
├── client.py          # PlankaClient — stdlib urllib HTTP wrapper, password auth
├── models.py          # Pydantic report models (public API surface)
├── reports.py         # PlankaReportGenerator — builds CardSummary/ActionSummary
├── server.py          # FastMCP server, one @mcp.tool() per query
└── main.py            # argparse CLI
main.py                # top-level shim that calls planka_checker.main:run
tests/test_reports.py  # 5 tests using MagicMock — no network
```

## Critical: Planka 2.x specifics

This codebase was built and tested against **Planka 2.0.0-rc.3** (the only version
on the live instance at `https://planka.aiesecmoscow.whaleharbor.net/`).
Older Planka v1 has a different schema. If you ever target a different
Planka version, re-verify these:

1. **Comments are NOT in the actions endpoint.** Planka v2 returns text
   comments from `/api/cards/{id}/comments` (with `id, createdAt, text, cardId, userId`).
   The actions endpoint `/api/cards/{id}/actions` returns only
   `createCard`, `moveCard`, `completeTask`, `uncompleteTask`,
   `addMemberToCard`, `removeMemberFromCard`.
   → See `client.py:PlankaClient.get_card_activity()` which merges both.

2. **plankapy is incompatible.** The PyPI `plankapy` (2.2.2) is built for
   Planka v1 and its `User`/`Card` dataclasses reject Planka v2 fields
   like `role`, `taskLists`, `customFieldGroups`, `customFieldValues`, etc.
   We removed plankapy and telethon from `pyproject.toml`. **Do not
   re-add plankapy** unless you verify compatibility first.

3. **Board `included` payload keys changed.** v2 includes
   `taskLists`, `customFieldGroups`, `customFields`, `customFieldValues`
   in addition to v1's set. `client.py:_parse_board_response` only reads
   the keys it needs, so this is safe.

4. **Field renames in cards**: v1 had `isDueDateCompleted`; v2 still has it
   on cards but the response may also surface other flags. Don't assume
   the field set is stable.

## Performance

A full report requires 2 sequential API calls per card (actions + comments),
plus 1 for the board itself. For a 49-card board that's ~99 calls × ~0.8s ≈
80s on a real Planka instance. The 2-minute default test timeout is too
short for dual-board reports — use background processes for full-board tests.

Key optimisations already applied:
- `PlankaReportGenerator` caches the world (`_cached_world`) so
  `generate_report()` only does the network work once. Don't undo this —
  the previous version rebuilt the world 4× per call (once per helper).
- HTTP keep-alive via a single `urllib` opener (~6% speedup, not huge).
- Lazy `self.client` property — instantiating the generator does NOT
  trigger a network call. The auth call only happens on first request.

**Future perf work** (not done): concurrent requests, or fetch comments
lazily only for cards that need `last_comment` (most cards don't have
any text comments).

## Running

```bash
# Install
uv sync
uv pip install -e .

# Tests
.venv/bin/python -m pytest -v

# CLI (env vars or .env)
PLANKA_URL=... PLANKA_USERNAME=... PLANKA_PASSWORD=... \
PLANKA_BOARD_URLS="https://.../boards/ID1,https://.../boards/ID2" \
.venv/bin/python -m planka_checker.main daily --summarize

# MCP server (stdio)
.venv/bin/python -m planka_checker.server
```

## Configuration

`PlankaSettings` reads from env / `.env` (case-insensitive):
- `PLANKA_URL` (required) — base URL, trailing slash stripped
- `PLANKA_USERNAME` / `PLANKA_PASSWORD` (required) — email or username
- `PLANKA_BOARD_URLS` (required for reports) — comma-separated full board URLs
  or bare board IDs. `settings.board_ids` extracts the numeric IDs.
- `BURNING_HOURS` (default 48) — due-within window
- `FORGOTTEN_DAYS` (default 7) — overdue + no-activity threshold

`planka_board_urls` uses `pydantic_settings.NoDecode` so the comma-separated
string isn't JSON-decoded. If you change the field type, preserve that
behaviour or tests / env loading will break.

## Semantics to preserve

- **is_completed**: True only if the card has ≥1 task AND all tasks are completed.
  A card with zero tasks is NOT considered "completed".
- **is_overdue**: `due_date < now` AND not completed AND `is_due_date_completed=False`.
  Past dates with `is_due_date_completed=True` are explicitly excluded
  (the user has marked the date as done).
- **is_burning**: future due date within `BURNING_HOURS` AND not completed
  AND due-date not completed.
- **is_forgotten**: overdue AND (`last_activity is None` OR
  `last_activity.created_at <= now - FORGOTTEN_DAYS`).
- **last_activity**: most recent action of ANY type (including
  completeTask, addMemberToCard, etc.).
- **last_comment**: most recent `commentCard` action specifically. May
  be null if the card has no comments.

## Testing tips

- Tests use `unittest.mock.patch.object(PlankaReportGenerator, "client", ...)`
  to inject a `MagicMock` client. Don't add network-dependent tests —
  there's no test Planka and the real one is slow + private.
- When adding a new test scenario, build cards/actions with the
  `PlankaCard` / `PlankaAction` dataclasses from `planka_models.py`,
  not the public Pydantic models.

## What NOT to do

- Don't re-add `plankapy` as a dependency.
- Don't introduce async/await — the codebase is sync. If you need concurrency,
  use `concurrent.futures.ThreadPoolExecutor` with the existing sync client.
- Don't change `PlankaReportGenerator` to rebuild the world per call —
  the cache exists for a reason.
- Don't add comments to code — the project owner has explicitly asked
  for no comments in the codebase.
