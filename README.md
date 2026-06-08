# planka-checker

A small MCP server (with CLI and Python library) for inspecting [Planka](https://github.com/plankanban/planka) board activity. It answers questions like:

- What changed on my boards in the last 24 hours / last week?
- Which cards are overdue?
- Which cards are due soon ("burning")?
- Which cards have been silently ignored for a long time ("forgotten")?

It's meant to be wired into an AI assistant (via MCP) or scripted from the shell, so you can get a quick status of one or more Planka boards without opening the web UI.

## What it does

Given one or more board URLs, planka-checker pulls the cards and their recent activity from the Planka REST API and produces a structured report containing:

- **Recent actions** — `createCard`, `moveCard`, `commentCard`, task toggles, member changes
- **Overdue cards** — past their due date and not yet completed
- **Burning cards** — due within the next 48 hours
- **Forgotten cards** — overdue AND silent for at least 7 days

Thresholds and the set of monitored boards are configurable.

## How to use it

Three ways to access the same reports:

1. **MCP server** (`planka-checker-mcp`) — connect it to Claude, Cursor, or any MCP-compatible client. Six tools: `get_daily_report`, `get_weekly_report`, `get_overdue_cards`, `get_burning_cards`, `get_forgotten_cards`, `get_full_report`.
2. **CLI** (`planka-checker {daily,weekly,overdue,burning,forgotten}`) — print a report as JSON, or a short human-readable summary.
3. **Python library** — `from planka_checker import PlankaSettings, PlankaReportGenerator` and call methods directly.

## Configuration

Copy `.env.example` to `.env` and fill in your Planka URL, login, and the boards you want to monitor. The same variables can also be passed as environment variables (see `.env.example` for the names).

## Status

Tested against Planka 2.0.0-rc.3 on a real instance. The current implementation is synchronous; an async migration is on the roadmap (see `AGENTS.md` for context).
