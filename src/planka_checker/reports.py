"""Report generation logic for Planka board activity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal

from planka_checker.client import PlankaClient
from planka_checker.config import PlankaSettings
from planka_checker.models import (
    ActionSummary,
    CardSummary,
    CommentInfo,
    PlankaReport,
    ReportMetadata,
)
from planka_checker.planka_models import (
    PlankaAction,
    PlankaBoardData,
    PlankaCard,
    PlankaUser,
    card_link,
)

ReportPeriod = Literal["day", "week"]
PERIOD_HOURS: dict[ReportPeriod, int] = {"day": 24, "week": 24 * 7}


@dataclass
class _World:
    """Aggregated, indexed state for one or more boards."""

    board_datas: list[PlankaBoardData] = field(default_factory=list)
    users_by_id: dict[int, PlankaUser] = field(default_factory=dict)
    boards_by_id: dict[int, PlankaBoardData] = field(default_factory=dict)
    actions_by_card: dict[int, list[PlankaAction]] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class PlankaReportGenerator:
    """Generate Planka activity reports for a configured set of boards."""

    def __init__(self, settings: PlankaSettings | None = None) -> None:
        self._settings = settings or PlankaSettings()
        self._client: PlankaClient | None = None

    @property
    def settings(self) -> PlankaSettings:
        return self._settings

    @property
    def client(self) -> PlankaClient:
        if self._client is None:
            self._client = PlankaClient(self._settings)
        return self._client

    async def _build_world(self) -> _World:
        board_ids = list(self._settings.board_ids)
        board_datas = await self.client.get_boards(board_ids)
        return await _build_world(board_datas, self.client)

    async def generate_report(self, period: ReportPeriod = "day") -> PlankaReport:
        """Generate a complete report for the configured boards.

        Args:
            period: Either ``"day"`` (last 24h) or ``"week"`` (last 7d).
        """
        if period not in PERIOD_HOURS:
            raise ValueError(
                f"Invalid period: {period!r}. Expected one of {list(PERIOD_HOURS)}"
            )

        world = await self._build_world()

        hours = PERIOD_HOURS[period]
        window_start = _now() - timedelta(hours=hours)
        recent_actions = _filter_actions_by_period(
            _iter_actions(world), window_start
        )

        overdue = self._get_overdue_cards(world)
        burning = self._get_burning_cards(world)
        forgotten = self._get_forgotten_cards(world)

        action_summaries = [
            _to_action_summary(action, world) for action in recent_actions
        ]
        action_summaries.sort(key=lambda a: a.created_at, reverse=True)

        return PlankaReport(
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
            overdue_cards=overdue,
            burning_cards=burning,
            forgotten_cards=forgotten,
        )

    async def get_overdue_cards(self) -> list[CardSummary]:
        world = await self._build_world()
        return self._get_overdue_cards(world)

    async def get_burning_cards(self) -> list[CardSummary]:
        world = await self._build_world()
        return self._get_burning_cards(world)

    async def get_forgotten_cards(self) -> list[CardSummary]:
        world = await self._build_world()
        return self._get_forgotten_cards(world)

    def _get_overdue_cards(self, world: _World) -> list[CardSummary]:
        summaries = [_build_card_summary(card, world) for card in _iter_cards(world)]
        return [s for s in summaries if _is_overdue(s)]

    def _get_burning_cards(self, world: _World) -> list[CardSummary]:
        summaries = [_build_card_summary(card, world) for card in _iter_cards(world)]
        threshold = _now() + timedelta(hours=self._settings.burning_hours)
        result: list[CardSummary] = []
        for summary in summaries:
            if not summary.due_date or summary.is_completed or summary.is_due_date_completed:
                continue
            if summary.due_date <= threshold and summary.due_date >= _now() - timedelta(minutes=1):
                result.append(summary)
        result.sort(key=lambda c: c.due_date or _now())
        return result

    def _get_forgotten_cards(self, world: _World) -> list[CardSummary]:
        summaries = [_build_card_summary(card, world) for card in _iter_cards(world)]
        threshold = _now() - timedelta(days=self._settings.forgotten_days)
        result: list[CardSummary] = []
        for summary in summaries:
            if not _is_overdue(summary):
                continue
            if summary.last_activity is None:
                result.append(summary)
                continue
            if summary.last_activity.created_at <= threshold:
                result.append(summary)
        result.sort(
            key=lambda c: c.last_activity.created_at if c.last_activity else c.updated_at
        )
        return result


async def _build_world(
    board_datas: list[PlankaBoardData], client: PlankaClient
) -> _World:
    users_by_id: dict[int, PlankaUser] = {}
    boards_by_id: dict[int, PlankaBoardData] = {}
    for data in board_datas:
        boards_by_id[data.board.id] = data
        for user in data.users.values():
            users_by_id.setdefault(user.id, user)

    pairs: list[tuple[int, int]] = []
    for data in board_datas:
        for card in data.cards.values():
            pairs.append((card.id, card.comments_total))

    activity_results = await asyncio.gather(
        *(
            client.get_card_activity(card_id, comments_total=comments_total)
            for card_id, comments_total in pairs
        )
    )
    actions_by_card: dict[int, list[PlankaAction]] = {
        card_id: list(actions)
        for (card_id, _), actions in zip(pairs, activity_results)
    }

    return _World(
        board_datas=board_datas,
        users_by_id=users_by_id,
        boards_by_id=boards_by_id,
        actions_by_card=actions_by_card,
    )


def _iter_cards(world: _World) -> Iterable[PlankaCard]:
    for data in world.board_datas:
        yield from data.cards.values()


def _iter_actions(world: _World) -> Iterable[PlankaAction]:
    for actions in world.actions_by_card.values():
        yield from actions


def _filter_actions_by_period(
    actions: Iterable[PlankaAction], window_start: datetime
) -> list[PlankaAction]:
    result: list[PlankaAction] = []
    for action in actions:
        if action.created_at is None:
            continue
        if _ensure_aware(action.created_at) >= window_start:
            result.append(action)
    return result


def _to_comment_info(action: PlankaAction, world: _World) -> CommentInfo:
    user = world.users_by_id.get(action.user_id)
    return CommentInfo(
        action_id=action.id,
        type=action.type,  # type: ignore[arg-type]
        user_id=action.user_id,
        user_name=user.name if user and user.name else "Unknown",
        created_at=_ensure_aware(action.created_at or _now()),
    )


def _to_action_summary(action: PlankaAction, world: _World) -> ActionSummary:
    data = _find_board_data_for_card(world, action.card_id)
    card = data.cards.get(action.card_id) if data else None
    base_url = data.board.base_url if data else ""
    card_name = card.name if card else "Unknown"
    user = world.users_by_id.get(action.user_id)
    user_name = user.name if user and user.name else "Unknown"
    has_text = bool(action.data.get("text")) if action.type == "commentCard" else False
    extra = {k: v for k, v in action.data.items() if k != "text"} if action.data else {}
    return ActionSummary(
        id=action.id,
        type=action.type,  # type: ignore[arg-type]
        card_id=action.card_id,
        card_name=card_name,
        card_url=card_link(base_url, action.card_id) if base_url else "",
        user_id=action.user_id,
        user_name=user_name,
        board_id=data.board.id if data else 0,
        board_name=data.board.name if data else "",
        created_at=_ensure_aware(action.created_at or _now()),
        extra_data=extra,
        has_comment_text=has_text,
    )


def _find_board_data_for_card(world: _World, card_id: int) -> PlankaBoardData | None:
    for data in world.board_datas:
        if card_id in data.cards:
            return data
    return None


def _build_card_summary(card: PlankaCard, world: _World) -> CardSummary:
    data = _find_board_data_for_card(world, card.id)
    board_name = data.board.name if data else ""

    list_obj = data.lists.get(card.list_id) if data else None
    list_name = list_obj.name if list_obj else ""

    label_names = [
        data.labels[lid].name for lid in card.label_ids if data and lid in data.labels and data.labels[lid].name
    ]
    member_names = [
        world.users_by_id[uid].name
        for uid in card.member_ids
        if uid in world.users_by_id and world.users_by_id[uid].name
    ]

    tasks = [data.tasks[tid] for tid in card.task_ids if data and tid in data.tasks] if data else []
    is_completed = bool(tasks) and all(t.is_completed for t in tasks)

    created_at = _ensure_aware(card.created_at or _now())
    updated_at = _ensure_aware(card.updated_at or created_at)
    due_date = _ensure_aware(card.due_date) if card.due_date else None

    card_actions = world.actions_by_card.get(card.id, [])
    last_comment: CommentInfo | None = None
    last_activity: CommentInfo | None = None
    for action in card_actions:
        info = _to_comment_info(action, world)
        if action.is_comment and last_comment is None:
            last_comment = info
        if last_activity is None or info.created_at > last_activity.created_at:
            last_activity = info

    if last_activity is not None:
        delta = _now() - last_activity.created_at
        days_since = max(0, int(delta.total_seconds() // 86400))
    else:
        days_since = None

    return CardSummary(
        id=card.id,
        name=card.name,
        url=card_link(data.board.base_url, card.id) if data else "",
        board_id=card.board_id,
        board_name=board_name,
        list_id=card.list_id,
        list_name=list_name,
        due_date=due_date,
        is_due_date_completed=card.is_due_date_completed,
        is_completed=is_completed,
        labels=label_names,
        members=member_names,
        created_at=created_at,
        updated_at=updated_at,
        last_comment=last_comment,
        last_activity=last_activity,
        days_since_last_activity=days_since,
    )


def _is_overdue(summary: CardSummary) -> bool:
    if not summary.due_date or summary.is_completed or summary.is_due_date_completed:
        return False
    return summary.due_date < _now()
