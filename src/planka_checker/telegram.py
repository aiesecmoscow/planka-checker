"""Telegram delivery of Planka reports (used by the CLI)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Iterable

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from planka_checker.models import CardSummary, PlankaReport

if TYPE_CHECKING:
    from planka_checker.config import PlankaSettings


TELEGRAM_MESSAGE_LIMIT = 4096


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _format_card_line(card: CardSummary) -> str:
    name = _escape_html(card.name)
    parts = [f"• <a href=\"{card.url}\">{name}</a>"]
    if card.list_name:
        parts.append(f"(<i>{_escape_html(card.list_name)}</i>)")
    meta: list[str] = []
    if card.due_date:
        meta.append(f"due {_escape_html(card.due_date.strftime('%Y-%m-%d %H:%M'))}")
    if card.members:
        meta.append("members: " + ", ".join(_escape_html(m) for m in card.members))
    if card.days_since_last_activity is not None:
        meta.append(f"{card.days_since_last_activity}d silent")
    if meta:
        parts.append("— " + "; ".join(meta))
    if card.board_name:
        board_label = _escape_html(card.board_name)
        if card.board_url:
            parts.append(
                f"board: <a href=\"{card.board_url}\">{board_label}</a>"
            )
        else:
            parts.append(f"board: {board_label}")
    return " ".join(parts)


def _format_section(title: str, cards: Iterable[CardSummary]) -> list[str]:
    cards = list(cards)
    if not cards:
        return []
    lines = [f"\n<b>{_escape_html(title)} ({len(cards)}):</b>"]
    for c in cards[:30]:
        lines.append(_format_card_line(c))
    if len(cards) > 30:
        lines.append(f"<i>…and {len(cards) - 30} more</i>")
    return lines


def _format_report(report: PlankaReport) -> str:
    meta = report.metadata
    period = _escape_html(meta.period)
    generated = _escape_html(meta.generated_at.strftime("%Y-%m-%d %H:%M"))
    header = (
        f"<b>Planka {period} report</b>\n"
        f"<i>Generated {generated}</i>\n"
        f"Boards: {meta.boards_count} · Actions: {meta.actions_count} · "
        f"Overdue: {meta.overdue_count} · Burning: {meta.burning_count} · "
        f"Forgotten: {meta.forgotten_count}"
    )
    sections: list[str] = [header]
    if report.actions:
        recent = report.actions[:10]
        lines = [f"\n<b>Recent actions ({len(report.actions)}):</b>"]
        for a in recent:
            ts = _escape_html(a.created_at.strftime("%Y-%m-%d %H:%M"))
            board_suffix = ""
            if a.board_name:
                board_label = _escape_html(a.board_name)
                if a.board_url:
                    board_suffix = f" (<a href=\"{a.board_url}\">{board_label}</a>)"
                else:
                    board_suffix = f" ({board_label})"
            lines.append(
                f"• {ts} {_escape_html(a.user_name)} "
                f"<code>{_escape_html(a.type)}</code> "
                f"<a href=\"{a.card_url}\">{_escape_html(a.card_name)}</a>"
                f"{board_suffix}"
            )
        if len(report.actions) > 10:
            lines.append(f"<i>…and {len(report.actions) - 10} more</i>")
        sections.extend(lines)
    sections.extend(_format_section("Overdue", report.overdue_cards))
    sections.extend(_format_section("Burning", report.burning_cards))
    sections.extend(_format_section("Forgotten", report.forgotten_cards))
    return "\n".join(sections)


def _format_card_list(kind: str, cards: list[CardSummary]) -> str:
    if not cards:
        return f"<b>Planka {kind}</b>\n<i>No cards.</i>"
    header = f"<b>Planka {kind} cards ({len(cards)}):</b>"
    return "\n".join([header, *_format_section(kind, cards)])


def format_message(data: object, kind: str) -> str:
    """Render a report / card list as a Telegram-safe HTML message."""
    if isinstance(data, PlankaReport):
        return _format_report(data)
    if isinstance(data, list) and all(isinstance(x, CardSummary) for x in data):
        return _format_card_list(kind, data)  # type: ignore[arg-type]
    return (
        f"<b>Planka {kind}</b>\n"
        "<i>(Report generated, but it had no displayable content.)</i>"
    )


def _split_message(text: str) -> list[str]:
    if len(text) <= TELEGRAM_MESSAGE_LIMIT:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > TELEGRAM_MESSAGE_LIMIT:
        cut = remaining.rfind("\n", 0, TELEGRAM_MESSAGE_LIMIT)
        if cut == -1:
            cut = TELEGRAM_MESSAGE_LIMIT
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def _build_keyboard(cards: list[CardSummary]) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    seen_boards: set[str] = set()
    for c in cards[:10]:
        if c.board_url and c.board_url not in seen_boards:
            seen_boards.add(c.board_url)
            label = f"📋 {c.board_name[:32]}" if c.board_name else "Open board"
            rows.append([InlineKeyboardButton(text=label, url=c.board_url)])
    for c in cards[:10]:
        if c.url:
            rows.append([InlineKeyboardButton(text=f"🔗 {c.name[:48]}", url=c.url)])
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _resolve_chat_id(chat_id: str) -> int | str:
    chat_id = chat_id.strip()
    if chat_id.lstrip("-").isdigit():
        return int(chat_id)
    return chat_id


async def send_telegram(
    settings: "PlankaSettings",
    data: object,
    kind: str,
) -> None:
    """Format `data` and send it to the configured Telegram chat.

    Raises ``RuntimeError`` if the bot token / chat id are missing,
    ``TelegramAPIError`` (from aiogram) on transport failures.
    """
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Configure it via env / .env to use --send-telegram."
        )
    if not settings.telegram_chat_id:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is not set. Configure it via env / .env to use --send-telegram."
        )

    message = format_message(data, kind)
    chunks = _split_message(message)
    chat_id = _resolve_chat_id(settings.telegram_chat_id)

    cards: list[CardSummary] = []
    if isinstance(data, PlankaReport):
        cards = list(data.overdue_cards) + list(data.burning_cards) + list(
            data.forgotten_cards
        )
    elif isinstance(data, list) and all(isinstance(x, CardSummary) for x in data):
        cards = list(data)  # type: ignore[arg-type]
    keyboard = _build_keyboard(cards)

    bot = Bot(token=settings.telegram_bot_token, parse_mode=ParseMode.HTML)
    try:
        for index, chunk in enumerate(chunks):
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                message_thread_id=settings.telegram_message_thread_id,
                disable_web_page_preview=True,
                reply_markup=keyboard if index == len(chunks) - 1 else None,
            )
    finally:
        await bot.session.close()


def send_telegram_sync(
    settings: "PlankaSettings",
    data: object,
    kind: str,
) -> None:
    """Synchronous wrapper around :func:`send_telegram` for the CLI entry point."""
    try:
        asyncio.run(send_telegram(settings, data, kind))
    except TelegramAPIError as exc:
        raise RuntimeError(f"Telegram API error: {exc}") from exc
