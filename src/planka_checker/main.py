"""Command-line entry point for planka-checker."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Sequence

from planka_checker.config import PlankaSettings
from planka_checker.reports import PlankaReportGenerator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planka-checker",
        description=(
            "Generate Planka activity reports from the command line. "
            "Configuration is loaded from environment variables / .env."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily", help="Print a daily Planka report as JSON")
    weekly = sub.add_parser("weekly", help="Print a weekly Planka report as JSON")
    overdue = sub.add_parser("overdue", help="Print overdue cards as JSON")
    burning = sub.add_parser("burning", help="Print burning cards as JSON")
    forgotten = sub.add_parser("forgotten", help="Print forgotten cards as JSON")

    for p in (daily, weekly, overdue, burning, forgotten):
        p.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print the JSON output",
        )
        p.add_argument(
            "--summarize",
            action="store_true",
            help="Print a short human-readable summary instead of raw JSON",
        )

    return parser


def _print(data: object, pretty: bool, summarize: bool, kind: str) -> None:
    if summarize:
        from planka_checker.models import CardSummary, PlankaReport

        if isinstance(data, PlankaReport):
            meta = data.metadata
            print(
                f"Planka report ({meta.period}) generated {meta.generated_at.isoformat()}\n"
                f"  Boards: {meta.boards_count}  Actions: {meta.actions_count}  "
                f"Overdue: {meta.overdue_count}  Burning: {meta.burning_count}  "
                f"Forgotten: {meta.forgotten_count}"
            )
            if data.actions:
                print("\nRecent actions:")
                for a in data.actions[:10]:
                    print(
                        f"  - {a.created_at.isoformat()}  {a.user_name}  "
                        f"{a.type}  {a.card_name}"
                    )
            for label, items in (
                ("Overdue", data.overdue_cards),
                ("Burning", data.burning_cards),
                ("Forgotten", data.forgotten_cards),
            ):
                if items:
                    print(f"\n{label} cards ({len(items)}):")
                    for c in items:
                        _summarize_card(c)
            return
        if isinstance(data, list) and all(isinstance(d, CardSummary) for d in data):
            print(f"{kind} cards ({len(data)}):")
            for c in data:  # type: ignore[arg-type]
                _summarize_card(c)
            return
    indent = 2 if pretty else None
    print(json.dumps(data, indent=indent, default=str, ensure_ascii=False))


def _summarize_card(card) -> None:  # type: ignore[no-untyped-def]
    parts = [f"  - [{card.id}] {card.name}"]
    if card.due_date:
        parts.append(f"due {card.due_date.isoformat()}")
    if card.members:
        parts.append(f"members: {', '.join(card.members)}")
    if card.last_activity:
        parts.append(
            f"last activity: {card.last_activity.type} "
            f"by {card.last_activity.user_name} "
            f"{card.last_activity.created_at.isoformat()}"
        )
    if card.days_since_last_activity is not None:
        parts.append(f"{card.days_since_last_activity}d silent")
    parts.append(card.url)
    print(" ".join(parts))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings = PlankaSettings()
    generator = PlankaReportGenerator(settings)

    if args.command == "daily":
        data = asyncio.run(generator.generate_report(period="day"))
        _print(data, args.pretty, args.summarize, "Daily")
    elif args.command == "weekly":
        data = asyncio.run(generator.generate_report(period="week"))
        _print(data, args.pretty, args.summarize, "Weekly")
    elif args.command == "overdue":
        data = asyncio.run(generator.get_overdue_cards())
        _print(data, args.pretty, args.summarize, "Overdue")
    elif args.command == "burning":
        data = asyncio.run(generator.get_burning_cards())
        _print(data, args.pretty, args.summarize, "Burning")
    elif args.command == "forgotten":
        data = asyncio.run(generator.get_forgotten_cards())
        _print(data, args.pretty, args.summarize, "Forgotten")
    else:
        parser.error(f"Unknown command: {args.command}")
    return 0


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
