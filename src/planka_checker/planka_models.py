"""Lightweight dataclasses for raw Planka API responses.

These mirror the fields the Planka Checker actually consumes and ignore
the rest. They are deliberately permissive: unknown fields are dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_bool(value: Any, default: bool = False) -> bool:
    return bool(value) if value is not None else default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(slots=True)
class PlankaUser:
    id: int = 0
    name: str = ""
    username: str = ""
    email: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PlankaUser":
        return cls(
            id=_as_int(data.get("id")) or 0,
            name=_as_str(data.get("name")),
            username=_as_str(data.get("username")),
            email=_as_str(data.get("email")),
        )


@dataclass(slots=True)
class PlankaLabel:
    id: int = 0
    name: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PlankaLabel":
        return cls(id=_as_int(data.get("id")) or 0, name=_as_str(data.get("name")))


@dataclass(slots=True)
class PlankaList:
    id: int = 0
    name: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PlankaList":
        return cls(id=_as_int(data.get("id")) or 0, name=_as_str(data.get("name")))


@dataclass(slots=True)
class PlankaTask:
    id: int = 0
    name: str = ""
    is_completed: bool = False
    card_id: int = 0

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PlankaTask":
        return cls(
            id=_as_int(data.get("id")) or 0,
            name=_as_str(data.get("name")),
            is_completed=_as_bool(data.get("isCompleted")),
            card_id=_as_int(data.get("cardId")) or 0,
        )


@dataclass(slots=True)
class PlankaCard:
    id: int = 0
    name: str = ""
    description: str = ""
    board_id: int = 0
    list_id: int = 0
    creator_user_id: int = 0
    due_date: datetime | None = None
    is_due_date_completed: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    comments_total: int = 0
    label_ids: list[int] = field(default_factory=list)
    member_ids: list[int] = field(default_factory=list)
    task_ids: list[int] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PlankaCard":
        return cls(
            id=_as_int(data.get("id")) or 0,
            name=_as_str(data.get("name")),
            description=_as_str(data.get("description")),
            board_id=_as_int(data.get("boardId")) or 0,
            list_id=_as_int(data.get("listId")) or 0,
            creator_user_id=_as_int(data.get("creatorUserId")) or 0,
            due_date=_as_dt(data.get("dueDate")),
            is_due_date_completed=_as_bool(data.get("isDueDateCompleted")),
            created_at=_as_dt(data.get("createdAt")),
            updated_at=_as_dt(data.get("updatedAt")),
            comments_total=_as_int(data.get("commentsTotal")) or 0,
        )


@dataclass(slots=True)
class PlankaAction:
    id: int = 0
    type: str = "commentCard"
    card_id: int = 0
    user_id: int = 0
    created_at: datetime | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_comment(self) -> bool:
        return self.type == "commentCard"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PlankaAction":
        return cls(
            id=_as_int(data.get("id")) or 0,
            type=_as_str(data.get("type"), default="commentCard"),
            card_id=_as_int(data.get("cardId")) or 0,
            user_id=_as_int(data.get("userId")) or 0,
            created_at=_as_dt(data.get("createdAt")),
            data=_as_dict(data.get("data")),
        )

    @classmethod
    def from_comment(
        cls, comment: dict[str, Any], *, card_id: int
    ) -> "PlankaAction":
        """Build a synthetic commentCard action from a /api/cards/{id}/comments payload."""
        return cls(
            id=_as_int(comment.get("id")) or 0,
            type="commentCard",
            card_id=card_id,
            user_id=_as_int(comment.get("userId")) or 0,
            created_at=_as_dt(comment.get("createdAt")),
            data={"text": _as_str(comment.get("text"))},
        )


@dataclass(slots=True)
class PlankaBoard:
    id: int = 0
    name: str = ""
    project_id: int = 0
    base_url: str = ""

    @property
    def link(self) -> str:
        return f"{self.base_url}boards/{self.id}"

    @classmethod
    def from_api(cls, data: dict[str, Any], base_url: str = "") -> "PlankaBoard":
        return cls(
            id=_as_int(data.get("id")) or 0,
            name=_as_str(data.get("name")),
            project_id=_as_int(data.get("projectId")) or 0,
            base_url=base_url,
        )


@dataclass(slots=True)
class PlankaBoardData:
    """A board plus all the related records the API returns in its ``included`` payload."""

    board: PlankaBoard
    users: dict[int, PlankaUser] = field(default_factory=dict)
    lists: dict[int, PlankaList] = field(default_factory=dict)
    labels: dict[int, PlankaLabel] = field(default_factory=dict)
    cards: dict[int, PlankaCard] = field(default_factory=dict)
    tasks: dict[int, PlankaTask] = field(default_factory=dict)
    card_label_map: dict[int, list[int]] = field(default_factory=dict)
    card_member_map: dict[int, list[int]] = field(default_factory=dict)
    card_task_map: dict[int, list[int]] = field(default_factory=dict)

    def actions_for(self, card_id: int) -> list[PlankaAction]:
        return _card_action_cache.get(card_id, [])


_card_action_cache: dict[int, list[PlankaAction]] = {}


def set_action_cache(card_id: int, actions: list[PlankaAction]) -> None:
    """Store the most recently fetched actions for a card (used by the report generator)."""
    _card_action_cache[card_id] = actions


def card_link(base_url: str, card_id: int) -> str:
    return f"{base_url}cards/{card_id}"
