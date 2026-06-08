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
├── client.py          # PlankaClient — httpx.AsyncClient wrapper, password auth
├── models.py          # Pydantic report models (public API surface)
├── reports.py         # PlankaReportGenerator — async, builds CardSummary/ActionSummary
├── server.py          # FastMCP server, one @mcp.tool() per query (async)
└── main.py            # argparse CLI; wraps the async generator with asyncio.run
main.py                # top-level shim that calls planka_checker.main:run
tests/test_reports.py  # 6 async tests using AsyncMock — no network
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
80s on a real Planka instance. With the async migration below, a 5-board
real report (covering ~7 cards with overdue activity) completes in ~2s on
the live instance — well under the 5–10s target.

Optimisations already applied:
- HTTP keep-alive via a single shared `httpx.AsyncClient` (connection
  pooling + keep-alive out of the box).
- Lazy `self.client` property — instantiating the generator does NOT
  trigger a network call. The auth call only happens on first request.
- `asyncio.Semaphore(20)` inside `PlankaClient._request` bounds
  concurrent fan-out against the Planka instance regardless of who
  calls the client.
- `get_card_activity(comments_total=0)` skips the `/comments` request
  entirely — free 50%+ speedup on boards where most cards have no
  comments. `PlankaCard.comments_total` is populated from
  `commentsTotal` in the board's `included` payload.

## Async migration (done)

The 80–135s wall-clock time on a real Planka was dominated by I/O wait
(per-card GETs at ~0.8s each). The async migration is complete:

- `urllib.request` replaced with a shared `httpx.AsyncClient` (built
  lazily, reused across all calls).
- `PlankaClient` methods are `async def` and return awaitables:
  `get_board`, `get_boards`, `get_card_actions`, `get_card_comments`,
  `get_card_activity`.
- `PlankaReportGenerator` exposes `async def generate_report(...)`,
  `async def get_overdue_cards()`, `async def get_burning_cards()`,
  `async def get_forgotten_cards()`. No sync wrappers.
- Per-card activity fetches are fanned out with `asyncio.gather(...)`;
  the semaphore in `_request` caps concurrency at 20.
- `get_card_comments` is skipped for cards whose `commentsTotal` is 0.
- FastMCP tool functions are `async def`; no transport changes needed.
- CLI keeps the same surface and uses `asyncio.run(...)` at the entry.
- Tests use `pytest-asyncio` (mode `"auto"`) with `AsyncMock` from
  `unittest.mock`; the same `patch.object(PlankaReportGenerator,
  "client", new=...)` pattern from the sync era still works.

**World cache removed.** The previous `_cached_world` field and
`invalidate_cache()` method were deleted in the migration. The async
fan-out makes a fresh world per call cheap enough (target ~5–10s for a
49-card board) that the cache was judged overengineering at the current
project size. If a use case emerges that issues many tool calls per
second — or profiling on a real Planka shows the per-call rebuild
becomes a bottleneck — re-introducing a short-TTL world cache (e.g. a
`asyncio.Lock`-guarded `_cached_world` on the generator) is the natural
next step. The same idea extends to a per-card response cache in
`PlankaClient` (the auth token cache at `PlankaClient._token` is the
existing pattern to copy).

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
  to inject an `AsyncMock` client (methods are `async def`). Don't add
  network-dependent tests — there's no test Planka and the real one is
  slow + private.
- When adding a new test scenario, build cards/actions with the
  `PlankaCard` / `PlankaAction` dataclasses from `planka_models.py`,
  not the public Pydantic models.
- `pytest-asyncio` is configured with `asyncio_mode = "auto"`, so
  `async def test_…` functions are picked up without per-test markers.

## What NOT to do

- Don't re-add `plankapy` as a dependency.
- Don't add comments to code — the project owner has explicitly asked
  for no comments in the codebase.
