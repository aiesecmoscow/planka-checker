"""Configuration management for Planka Checker."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _extract_board_id(url: str) -> int:
    """Extract a board ID from a Planka board URL.

    Accepts URLs like:
      - https://planka.example.com/boards/1705889625190434326
      - https://planka.example.com/boards/1705889625190434326/
      - /boards/1705889625190434326
      - 1705889625190434326 (raw ID)
    """
    url = url.strip()
    if not url:
        raise ValueError("Empty board URL/id")

    if url.isdigit():
        return int(url)

    parts = [p for p in url.rstrip("/").split("/") if p]
    if not parts:
        raise ValueError(f"Cannot extract board id from URL: {url!r}")

    last = parts[-1]
    if not last.isdigit():
        raise ValueError(f"Cannot extract board id from URL: {url!r}")
    return int(last)


class PlankaSettings(BaseSettings):
    """Planka connection and report configuration loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    planka_url: Annotated[str, Field(description="Base URL of the Planka instance")]
    planka_username: Annotated[str, Field(description="Planka username or email")]
    planka_password: Annotated[str, Field(description="Planka password")]

    planka_board_urls: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Comma-separated list of Planka board URLs to monitor. "
            "Board IDs are extracted from the URL path."
        ),
    )

    burning_hours: Annotated[
        int,
        Field(
            default=48,
            ge=1,
            description="Tasks due within this many hours are considered 'burning'.",
        ),
    ] = 48

    forgotten_days: Annotated[
        int,
        Field(
            default=7,
            ge=1,
            description=(
                "Overdue cards with no activity for this many days are "
                "considered 'forgotten'."
            ),
        ),
    ] = 7

    telegram_bot_token: Annotated[
        str,
        Field(
            default="",
            description=(
                "Telegram bot token (from @BotFather). Required to send reports "
                "via --send-telegram."
            ),
        ),
    ] = ""

    telegram_chat_id: Annotated[
        str,
        Field(
            default="",
            description=(
                "Telegram chat ID (user, group or channel @username) where the "
                "report will be sent."
            ),
        ),
    ] = ""

    telegram_message_thread_id: Annotated[
        int | None,
        Field(
            default=None,
            description=(
                "Optional Telegram supergroup topic/thread id to send the "
                "message into. Leave empty for the general topic."
            ),
        ),
    ] = None

    @field_validator("planka_url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @field_validator("planka_board_urls", mode="before")
    @classmethod
    def _split_board_urls(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        raise ValueError("planka_board_urls must be a string or list of strings")

    @property
    def board_ids(self) -> list[int]:
        """Board IDs extracted from the configured board URLs."""
        return [_extract_board_id(url) for url in self.planka_board_urls]
