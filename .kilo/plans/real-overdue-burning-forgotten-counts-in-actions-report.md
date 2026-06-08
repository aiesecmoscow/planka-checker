# Plan: Real overdue/burning/forgotten counts in `get_actions` reports

## Goal

The lightweight `ActionsReport` produced by `PlankaReportGenerator.get_actions()`
and exposed via the `get_daily_actions` / `get_weekly_actions` MCP tools and
the `daily-actions` / `weekly-actions` CLI subcommands currently hardcodes
`overdue_count`, `burning_count`, and `forgotten_count` to `0`, even when
real overdue / burning / forgotten cards exist on the configured boards.

Make those three counts reflect reality in the existing `ReportMetadata`
output, **without** changing the report shape — `ActionsReport` continues
to expose only `metadata` + `actions` (no card lists).

## Why this is essentially free

`get_actions()` already calls `self._build_world()`, which:

- Fetches each board (1 call per board).
- Fans out per-card `/actions` and `/comments` requests with
  `asyncio.gather(...)` (capped at concurrency 20 by the client semaphore).
- Stores the merged activity in `world.actions_by_card`.

The exact same `_World` powers the three analysis passes
(`_get_overdue_cards`, `_get_burning_cards`, `_get_forgotten_cards`) that
`generate_report()` already runs. So the data is in memory at the end of
`get_actions()` — we just throw away the analytics results today.

Net cost: a few extra in-memory list comprehensions, no extra HTTP calls.

## Code changes

### 1. `src/planka_checker/reports.py` — `get_actions()`

Replace the hardcoded zeros with real counts. Reuse the existing helpers
that `generate_report()` already uses — no new logic.

```python
# in PlankaReportGenerator.get_actions()
world = await self._build_world()
hours = PERIOD_HOURS[period]
window_start = _now() - timedelta(hours=hours)
action_summaries = self._collect_recent_actions(world, window_start)

overdue = self._get_overdue_cards(world)
burning = self._get_burning_cards(world)
forgotten = self._get_forgotten_cards(world)

return ActionsReport(
    metadata=ReportMetadata(
        generated_at=_now(),
        period=period,
        board_ids=[data.board.id for data in world.board_datas],
        boards_count=len(world.board_datas),
        actions_count=len(action_summaries),
        overdue_count=len(overdue),
        burning_count=len(burning),
        forgotten_count=len(forgotten),
        burning_hours=self._settings.burning_hours,
        forgotten_days=self._settings.forgotten_days,
    ),
    actions=action_summaries,
)
```

This mirrors lines 89–104 of `generate_report()` — same three calls, but
we only keep the `len(...)` for the metadata. The `overdue` / `burning` /
`forgotten` lists are dropped on the floor (GC), so `ActionsReport`
remains slim (no card lists, no schema change).

### 2. `src/planka_checker/telegram.py` — `_format_actions_report()`

The Telegram header for `ActionsReport` today only shows
`Boards` / `Actions`. Extend it to also include the three counts when
non-zero, so `--send-telegram` is consistent with the full
`PlankaReport` header (`_format_report`, line 67).

```python
header = (
    f"<b>Planka {period} actions</b>\n"
    f"<i>Generated {generated}</i>\n"
    f"Boards: {meta.boards_count} · Actions: {meta.actions_count} · "
    f"Overdue: {meta.overdue_count} · Burning: {meta.burning_count} · "
    f"Forgotten: {meta.forgotten_count}"
)
```

This is a one-line addition; safe and additive.

### 3. `tests/test_reports.py` — `test_get_actions_daily`

This test currently asserts `overdue_count == 0`, `burning_count == 0`,
`forgotten_count == 0` (lines 209–211). With the fix, the same fixture
should report real counts. Update the assertions to match the fixture:

- 1 overdue card ("Overdue In Progress", `past_due`, not completed,
  `is_due_date_completed=False`) → `overdue_count == 1`.
- 1 burning card ("Burning Soon", `in_24h` ≈ +20h, within 48h default) →
  `burning_count == 1`.
- The same overdue card has a `moveCard` action at `week_ago`
  (`now - 8d`, which is more than `FORGOTTEN_DAYS=7`) → `forgotten_count == 1`.

Replace the three zero-asserts with:

```python
assert report.metadata.overdue_count == 1
assert report.metadata.burning_count == 1
assert report.metadata.forgotten_count == 1
```

Also reword the existing docstring on `get_actions` in `reports.py`
(lines 115–123) and the MCP tool descriptions in `server.py`
(`get_daily_actions` line 48, `get_weekly_actions` line 61) to drop the
"No overdue/burning/forgotten analysis" claim, replacing it with
"Lightweight activity feed — card lists are not included, but metadata
includes overdue/burning/forgotten counts."

## Files touched

- `src/planka_checker/reports.py` — drop three zeros, reuse three helpers, update docstring.
- `src/planka_checker/telegram.py` — extend `_format_actions_report` header.
- `src/planka_checker/server.py` — update two tool descriptions.
- `tests/test_reports.py` — update `test_get_actions_daily` assertions.

## Files NOT touched

- `models.py` — `ActionsReport` schema stays exactly the same.
- `client.py` — no HTTP behaviour change.
- `main.py` — CLI subcommands `daily-actions` / `weekly-actions` keep the
  same flags (`--pretty`, `--summarize`, `--send-telegram`); the
  summariser in `_print` already handles `ActionsReport` via
  `meta.actions_count` and will now show real numbers via
  `meta.overdue_count` etc. **However**, today's `_print` summariser
  only prints overdue/burning/forgotten sections for `PlankaReport`, not
  for `ActionsReport` (line 80: `isinstance(data, PlankaReport)`). That
  is intentional and stays the same — `ActionsReport` has no card lists
  to enumerate, so we only surface the counts in metadata. If we want
  the CLI summary to also show those counts for `ActionsReport`, that
  is a follow-up; the user's request is specifically about the counts in
  the report output, not a CLI summary redesign.

## Performance impact

- No additional HTTP calls. The three helpers are pure in-memory
  filtering over `world.actions_by_card` and the cards already loaded
  from the board payloads.
- The `_build_world()` cost is identical to today (per-card actions
  fanned out in parallel with the existing 20-concurrency semaphore).
- In practice the 2-board / ~7-card real run that today takes ~2s will
  remain ~2s; the only added work is three list comprehensions over
  ~7 cards.

## Verification

1. `uv sync` (already done in the dev env).
2. `.venv/bin/python -m pytest -v` — all 6 existing tests pass, with
   `test_get_actions_daily` updated to assert the real counts.
3. Manual smoke (optional, against the live Planka if env vars are set):
   `uvx --from . planka-checker daily-actions --summarize` should now
   show the real `Overdue / Burning / Forgotten` counts in the header
   line, and the Telegram-sent message should include them too.

## Out of scope

- Adding full overdue/burning/forgotten card lists to `ActionsReport`
  (would change the public MCP schema and defeat the "lightweight" goal
  — users who want lists should call `get_daily_report` or the dedicated
  `get_overdue_cards` / `get_burning_cards` / `get_forgotten_cards`
  tools).
- Changing `ActionsReport.metadata` field set.
- CLI summary redesign.
