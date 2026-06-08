# Async migration plan for planka-checker

## Goal

Convert the network-bound hot path in planka-checker from synchronous
sequential `urllib` calls to asynchronous concurrent `httpx` calls.

Per AGENTS.md, the real Planka instance is slow (~0.8s/call × 99 calls
per 49-card board ≈ 80s wall clock). The user wants the async migration
described there applied now, with these decisions:

- **Async-only public API.** `PlankaReportGenerator` methods become
  `async def`. No sync wrappers kept. Callers update accordingly.
- **No caching layer in the implementation.** Drop the existing
  `_cached_world` cache, `_build_world_fresh()`, `invalidate_cache()`,
  and the planned `asyncio.Lock` around world rebuild. The motivation
  is to keep the code minimal — caching is judged overengineering
  for the current size of the project. The idea is preserved in
  AGENTS.md as a potential future direction (see step 10 below).
- **Concurrency:** bound fan-out with `asyncio.Semaphore(20)`.

## Target speed

For a 49-card board: ~5–10s end-to-end (from ~80s today) when network
is the bottleneck, driven by 20-way concurrency over the per-card
actions+comments calls.

## What changes (per file)

### `pyproject.toml`

- Add `httpx>=0.27` to `[project].dependencies` (already pulled in
  transitively via `mcp[cli]`, but make it explicit so we don't lose it
  when mcp trims its extras).
- Add `pytest-asyncio>=0.23` to `[dependency-groups].dev`.
- Add `asyncio_mode = "auto"` under `[tool.pytest.ini_options]` so
  existing test functions become async-aware without per-test
  `@pytest.mark.asyncio` markers (matches the "one fixture pattern"
  in AGENTS.md).

### `src/planka_checker/client.py`

- Replace `urllib.request` with a single shared
  `httpx.AsyncClient(timeout=...)` built lazily in the constructor
  (httpx connection pooling + keep-alive out of the box).
- Drop the `_opener` field.
- Keep the existing `_token` cache (this is auth-state caching, not
  report caching, and is required for the client to work at all).
- Make all network methods `async def` and return awaitables:
  - `get_board(board_id) -> PlankaBoardData`
  - `get_boards(board_ids) -> list[PlankaBoardData]` — uses
    `asyncio.gather` over the per-board calls. Individual failures
    are still swallowed (preserve current behaviour).
  - `get_card_actions(card_id) -> list[PlankaAction]`
  - `get_card_comments(card_id) -> list[PlankaAction]`
  - `get_card_activity(card_id, *, comments_total: int | None = None)
    -> list[PlankaAction]` — if `comments_total == 0`, skip the
    `/comments` request entirely. This is the "free 50% speedup"
    called out in AGENTS.md — most cards on a typical board have
    zero comments.
- Add `_semaphore: asyncio.Semaphore` (limit 20) on the client,
  acquired inside every request method so we don't hammer Planka
  regardless of who calls us.
- The auth POST stays on the same `httpx.AsyncClient` (no special
  treatment needed).
- The internal `_request` helper becomes `async def _request(...)`
  using `client.get/post(...)` with the bearer header injected. The
  semaphore is acquired in `_request`.
- Map `httpx.HTTPStatusError` to `PlankaAPIError` to preserve the
  current error message shape
  (`f"Planka {method} {path} failed: {exc.code} {exc.reason}: {detail}"`).

### `src/planka_checker/planka_models.py`

- Add `comments_total: int = 0` to `PlankaCard` and populate it in
  `from_api` from `data.get("commentsTotal")` (default 0 if missing
  — safe for v1 too).
- No other model changes.

### `src/planka_checker/reports.py`

- Make all public generator methods `async def`:
  `generate_report`, `get_overdue_cards`, `get_burning_cards`,
  `get_forgotten_cards`.
- **Remove caching:** delete `_cached_world`, `invalidate_cache()`,
  and `_build_world_fresh()`. `_build_world` is now a single async
  method that always fetches fresh. Each public entry point calls
  it directly.
- Make `_build_world` async. The function:
  1. `await self.client.get_boards(board_ids)` (already async).
  2. Build a flat list of `(card_id, comments_total)` pairs from
     every `board_data.cards`.
  3. Fan out with
     `asyncio.gather(*(self.client.get_card_activity(cid, comments_total=ct) for cid, ct in pairs))`.
     The semaphore inside `get_card_activity` bounds concurrency
     to 20.
  4. Assemble the `_World` dataclass from the results.
- No `asyncio.Lock` (no cache to protect, so a single async task
  always rebuilds the world it needs).
- The `is_completed` / `is_overdue` / `is_burning` / `is_forgotten`
  / `last_activity` / `last_comment` semantics in AGENTS.md are
  **preserved** verbatim. No logic changes in `_build_card_summary`,
  `_get_overdue_cards`, `_get_burning_cards`,
  `_get_forgotten_cards`.
- `_to_action_summary` is unchanged.
- Note: dropping the world cache means `generate_report` does
  exactly 1 board fetch + 1 fan-out of per-card activity fetches,
  instead of the previous 4× rebuild pattern. With async
  concurrency at 20, that one fan-out is what gives us the
  speedup — caching was masking the original sequential
  architecture's waste, and going async makes it unnecessary.

### `src/planka_checker/server.py`

- Construct a fresh `PlankaReportGenerator(settings)` per tool call
  (keep the current `_generator()` pattern, no module-level cache).
  With no world cache, there's no benefit to reusing a generator
  across calls.
- Convert every `@mcp.tool()` body to `async def` and `await` the
  generator call. FastMCP supports `async def` tool functions
  natively; no further wiring needed.
- `get_full_report(period)` becomes async; validation against
  `("day", "week")` stays sync inside the body.

### `src/planka_checker/main.py`

- CLI entry: `main()` builds the generator, then
  `asyncio.run(...)` for whichever command. Keep the existing
  argparse surface so users see no change:
  `planka-checker daily --summarize` etc.
- No new flags.

### `tests/test_reports.py`

- Switch the `fake_client` fixture to `AsyncMock` from
  `unittest.mock`; configure `get_board`, `get_boards`,
  `get_card_actions`, `get_card_comments`, `get_card_activity` as
  `AsyncMock` (or as `MagicMock` with `AsyncMock` side_effects) so
  awaiting them returns the prepared data.
- Mark tests `async def`; use `await patched_generator.<method>()`.
- Keep the `patch.object(PlankaReportGenerator, "client", new=...)`
  pattern from AGENTS.md — works identically with an `AsyncMock`.
- The `test_settings_board_id_parsing` test stays sync.
- Add a tiny smoke test that `get_card_activity` does **not** call
  `get_card_comments` when `comments_total == 0` (assert with
  `fake_client.get_card_comments.assert_not_called()`), to lock in
  the AGENTS.md speedup.

### `AGENTS.md`

- Replace the "Performance" section's claim that
  "`PlankaReportGenerator` caches the world (`_cached_world`) so
  `generate_report()` only does the network work once. Don't undo
  this — the previous version rebuilt the world 4× per call (once
  per helper)." with a paragraph noting that the cache was
  removed in the async migration, and that re-introducing it is
  a possible future direction (alongside a short-TTL response
  cache) once profiling on a real Planka shows it's worth the
  complexity. Keep the other Performance bullets (HTTP keep-alive,
  lazy `self.client`, commentsTotal skip).

## Migration order (executable steps)

1. Edit `pyproject.toml`: add `httpx` dep, add `pytest-asyncio`
   dev dep, add `asyncio_mode = "auto"`. Run `uv sync`.
2. Edit `planka_models.py`: add `comments_total` to `PlankaCard` +
   `from_api`.
3. Edit `client.py`: rewrite to `httpx.AsyncClient` + async methods
   + semaphore + skip-comments-when-zero plumbing.
4. Edit `reports.py`: make generator methods async; drop
   `_cached_world` / `invalidate_cache` / `_build_world_fresh`;
   rebuild world concurrently.
5. Edit `server.py`: convert tool functions to `async def`; keep
   the per-call generator construction.
6. Edit `main.py`: wrap the generator call in `asyncio.run(...)`.
7. Update `tests/test_reports.py` to async + `AsyncMock`; add the
   skip-comments assertion.
8. Run `uv run pytest -v` until green.
9. Run `uv run python -c "import planka_checker; print('ok')"` as
   a smoke import.
10. Edit `AGENTS.md` to note that the world cache was removed and
    is a possible future direction.
11. Manual: invoke the CLI's `--help` to confirm the entrypoint
    surface is unchanged (no live Planka call needed for that).
    Skip the live `daily --summarize` run unless the test Planka
    is reachable.

## Out of scope

- No new MCP tools.
- No changes to `PlankaSettings`, `models.py`, `config.py`.
- No re-add of `plankapy`.
- No comments added to code (project rule).
- No `_cached_world` / world-level cache (per user decision).
- No short-TTL response cache on `get_card_actions` /
  `get_card_comments` (per user decision).
- No `asyncio.Lock` for world rebuild (no cache to protect).
- No sync wrapper kept on the public API (per user decision).

## Risk / rollback

- The async rewrite touches every layer (client → reports → server
  → CLI → tests). The behavioural surface is preserved, but a
  mid-migration state won't run. Land as one commit (or one
  branch) and revert that single commit if anything is broken.
- `httpx` is already in the venv transitively, so risk of "new
  dep" is low; pinning it explicitly in `pyproject.toml` is the
  only correctness concern.
- The MCP server is stdio-based; switching tools to `async def`
  is a no-op for FastMCP's transport, so no client-visible change.
- Dropping the world cache means every tool call re-fetches the
  full board set. With async concurrency this is fast enough
  (target ~5–10s) and keeps the code simple; if a use case
  emerges that does many tool calls per second, re-introduce
  the cache (the AGENTS.md note records the path).

## Validation

- `uv run pytest -v` must pass all existing 5 tests + the new
  skip-comments smoke test.
- `uv run python -c "import planka_checker.server"` must import
  cleanly.
- `uv run python -m planka_checker.main --help` must show the
  same subcommand list as today.
