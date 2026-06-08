"""MCP server exposing Planka activity reports as tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from planka_checker.config import PlankaSettings
from planka_checker.models import ActionsReport, CardSummary, PlankaReport
from planka_checker.reports import PlankaReportGenerator

mcp = FastMCP(
    "planka-checker",
    instructions=(
        "Tools for inspecting Planka board activity: daily/weekly reports, "
        "overdue tasks, burning (urgent) tasks, and forgotten tasks."
    ),
)


def _generator() -> PlankaReportGenerator:
    settings = PlankaSettings()
    return PlankaReportGenerator(settings)


@mcp.tool(
    name="get_daily_report",
    description=(
        "Return a full Planka report covering the last 24 hours of activity, "
        "including overdue/burning/forgotten cards, for the configured boards."
    ),
)
async def get_daily_report() -> dict:
    return (await _generator().generate_report(period="day")).model_dump(mode="json")


@mcp.tool(
    name="get_weekly_report",
    description=(
        "Return a full Planka report covering the last 7 days of activity, "
        "including overdue/burning/forgotten cards, for the configured boards."
    ),
)
async def get_weekly_report() -> dict:
    return (await _generator().generate_report(period="week")).model_dump(mode="json")


@mcp.tool(
    name="get_daily_actions",
    description=(
        "Return the card action changes (createCard, moveCard, "
        "commentCard, completeTask, addMemberToCard, etc.) from the last "
        "24 hours across the configured boards. Lightweight activity feed "
        "— card lists are not included, but metadata carries the real "
        "overdue/burning/forgotten counts."
    ),
)
async def get_daily_actions() -> dict:
    report: ActionsReport = await _generator().get_actions(period="day")
    return report.model_dump(mode="json")


@mcp.tool(
    name="get_weekly_actions",
    description=(
        "Return the card action changes (createCard, moveCard, "
        "commentCard, completeTask, addMemberToCard, etc.) from the last "
        "7 days across the configured boards. Lightweight activity feed "
        "— card lists are not included, but metadata carries the real "
        "overdue/burning/forgotten counts."
    ),
)
async def get_weekly_actions() -> dict:
    report: ActionsReport = await _generator().get_actions(period="week")
    return report.model_dump(mode="json")


@mcp.tool(
    name="get_overdue_cards",
    description=(
        "Return all overdue cards (past due date and not completed) across the "
        "configured boards. Each card is a full CardSummary with last activity."
    ),
)
async def get_overdue_cards() -> list[dict]:
    cards: list[CardSummary] = await _generator().get_overdue_cards()
    return [card.model_dump(mode="json") for card in cards]


@mcp.tool(
    name="get_burning_cards",
    description=(
        "Return all cards due within the configured BURNING_HOURS window "
        "across the configured boards."
    ),
)
async def get_burning_cards() -> list[dict]:
    cards: list[CardSummary] = await _generator().get_burning_cards()
    return [card.model_dump(mode="json") for card in cards]


@mcp.tool(
    name="get_forgotten_cards",
    description=(
        "Return all overdue cards that have had no activity for at least "
        "FORGOTTEN_DAYS across the configured boards."
    ),
)
async def get_forgotten_cards() -> list[dict]:
    cards: list[CardSummary] = await _generator().get_forgotten_cards()
    return [card.model_dump(mode="json") for card in cards]


@mcp.tool(
    name="get_full_report",
    description=(
        "Return a complete PlankaReport (metadata + actions + overdue + "
        "burning + forgotten cards) for the given period ('day' or 'week')."
    ),
)
async def get_full_report(period: str = "day") -> dict:
    if period not in ("day", "week"):
        raise ValueError(f"Invalid period: {period!r}. Expected 'day' or 'week'.")
    report: PlankaReport = await _generator().generate_report(period=period)
    return report.model_dump(mode="json")


def run() -> None:
    """Run the MCP server over stdio (entry point for console scripts)."""
    mcp.run()


if __name__ == "__main__":
    run()
