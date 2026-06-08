"""Unit tests for planka_checker report logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from planka_checker.config import PlankaSettings
from planka_checker.models import ActionsReport, PlankaReport
from planka_checker.planka_models import (
    PlankaAction,
    PlankaBoard,
    PlankaBoardData,
    PlankaCard,
    PlankaList,
    PlankaUser,
)
from planka_checker.reports import PlankaReportGenerator, _build_world


def _settings() -> PlankaSettings:
    return PlankaSettings(
        planka_url="https://planka.example.com",
        planka_username="user@example.com",
        planka_password="pw",
        planka_board_urls=["https://planka.example.com/boards/1"],
        burning_hours=48,
        forgotten_days=7,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _card(
    card_id: int,
    *,
    name: str = "Task",
    list_id: int = 1,
    due_date: datetime | None = None,
    is_due_date_completed: bool = False,
    member_ids: list[int] | None = None,
    label_ids: list[int] | None = None,
    task_ids: list[int] | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    comments_total: int = 0,
) -> PlankaCard:
    base = _now() - timedelta(days=30)
    return PlankaCard(
        id=card_id,
        name=name,
        board_id=1,
        list_id=list_id,
        due_date=due_date,
        is_due_date_completed=is_due_date_completed,
        created_at=created_at or base,
        updated_at=updated_at or base,
        member_ids=member_ids or [],
        label_ids=label_ids or [],
        task_ids=task_ids or [],
        comments_total=comments_total,
    )


def _action(
    card_id: int, type_: str, when: datetime, *, user_id: int = 11, action_id: int = 1, text: str | None = None
) -> PlankaAction:
    return PlankaAction(
        id=action_id,
        type=type_,
        card_id=card_id,
        user_id=user_id,
        created_at=when,
        data={"text": text} if text is not None else {},
    )


def _build_board_data(actions_by_card: dict[int, list[PlankaAction]]) -> PlankaBoardData:
    now = _now()
    yesterday = now - timedelta(hours=12)
    week_ago = now - timedelta(days=8)
    long_ago = now - timedelta(days=20)
    in_24h = now + timedelta(hours=20)
    in_72h = now + timedelta(hours=70)
    past_due = now - timedelta(days=3)

    overdue = _card(
        1001,
        name="Overdue In Progress",
        list_id=1,
        due_date=past_due,
        member_ids=[22],
        created_at=long_ago,
        updated_at=week_ago,
    )
    burning = _card(
        1002,
        name="Burning Soon",
        list_id=2,
        due_date=in_24h,
    )
    future = _card(
        1003,
        name="Future Task",
        list_id=3,
        due_date=in_72h,
    )
    completed = _card(
        1004,
        name="Completed Tasks Card",
        list_id=1,
        due_date=past_due,
        task_ids=[7001],
    )

    cards = {c.id: c for c in (overdue, burning, future, completed)}
    lists = {
        1: PlankaList(id=1, name="In Progress"),
        2: PlankaList(id=2, name="To Do"),
        3: PlankaList(id=3, name="Backlog"),
    }
    users = {
        11: PlankaUser(id=11, name="Alice"),
        22: PlankaUser(id=22, name="Bob"),
    }
    tasks = {7001: MagicMock(id=7001, is_completed=True, card_id=1004)}
    actions_by_card.setdefault(1001, [_action(1001, "moveCard", week_ago, action_id=5001)])
    actions_by_card.setdefault(
        1002,
        [_action(1002, "commentCard", yesterday, user_id=22, action_id=5002, text="Looking into this")],
    )
    actions_by_card.setdefault(
        1003, [_action(1003, "createCard", yesterday, action_id=5003)]
    )
    actions_by_card.setdefault(1004, [])

    return PlankaBoardData(
        board=PlankaBoard(id=1, name="Sprint Board", base_url="https://planka.example.com/"),
        users=users,
        lists=lists,
        cards=cards,
        tasks=tasks,
    )


@pytest.fixture
def patched_generator() -> PlankaReportGenerator:
    settings = _settings()
    actions: dict[int, list[PlankaAction]] = {}
    board_data = _build_board_data(actions)

    fake_client = MagicMock()
    fake_client.get_boards = AsyncMock(return_value=[board_data])
    fake_client.get_card_activity = AsyncMock(
        side_effect=lambda cid, comments_total=0: actions.get(cid, [])
    )
    fake_client.get_card_actions = AsyncMock(
        side_effect=lambda cid: actions.get(cid, [])
    )
    fake_client.get_card_comments = AsyncMock(return_value=[])

    with patch.object(PlankaReportGenerator, "client", new=fake_client):
        yield PlankaReportGenerator(settings)


async def test_overdue_cards(patched_generator: PlankaReportGenerator) -> None:
    overdue = await patched_generator.get_overdue_cards()
    names = {c.name for c in overdue}
    assert "Overdue In Progress" in names
    assert "Completed Tasks Card" not in names


async def test_burning_cards(patched_generator: PlankaReportGenerator) -> None:
    burning = await patched_generator.get_burning_cards()
    names = {c.name for c in burning}
    assert "Burning Soon" in names
    assert "Future Task" not in names
    assert "Overdue In Progress" not in names


async def test_forgotten_cards(patched_generator: PlankaReportGenerator) -> None:
    forgotten = await patched_generator.get_forgotten_cards()
    names = {c.name for c in forgotten}
    assert "Overdue In Progress" in names
    assert "Burning Soon" not in names


async def test_daily_report_includes_recent_actions(patched_generator: PlankaReportGenerator) -> None:
    report: PlankaReport = await patched_generator.generate_report(period="day")
    assert report.metadata.period == "day"
    assert report.metadata.actions_count == 2
    types = {a.type for a in report.actions}
    assert "commentCard" in types
    assert "createCard" in types
    assert report.metadata.forgotten_count >= 1
    assert report.metadata.burning_count >= 1


async def test_get_actions_daily(patched_generator: PlankaReportGenerator) -> None:
    report = await patched_generator.get_actions(period="day")
    assert isinstance(report, ActionsReport)
    assert report.metadata.period == "day"
    assert report.metadata.actions_count == 2
    assert report.metadata.overdue_count == 1
    assert report.metadata.burning_count == 1
    assert report.metadata.forgotten_count == 1
    assert {a.type for a in report.actions} == {"commentCard", "createCard"}
    assert all(a.board_id for a in report.actions)
    assert all(a.user_name for a in report.actions)
    assert [a.id for a in report.actions] == sorted(
        (a.id for a in report.actions),
        key=lambda aid: next(x.created_at for x in report.actions if x.id == aid),
        reverse=True,
    )


async def test_get_actions_weekly_includes_older(patched_generator: PlankaReportGenerator) -> None:
    report = await patched_generator.get_actions(period="week")
    assert isinstance(report, ActionsReport)
    assert report.metadata.period == "week"
    assert report.metadata.actions_count == 2
    types = {a.type for a in report.actions}
    assert {"commentCard", "createCard"} <= types
    assert report.metadata.actions_count >= 2


async def test_get_actions_invalid_period(patched_generator: PlankaReportGenerator) -> None:
    import pytest

    with pytest.raises(ValueError):
        await patched_generator.get_actions(period="month")  # type: ignore[arg-type]


def test_settings_board_id_parsing() -> None:
    s = PlankaSettings(
        planka_url="https://planka.example.com",
        planka_username="u@e.com",
        planka_password="pw",
        planka_board_urls=(
            "https://planka.example.com/boards/123,"
            "https://planka.example.com/boards/456/,/boards/789,42"
        ),
    )
    assert s.board_ids == [123, 456, 789, 42]


async def test_get_card_activity_skips_comments_when_zero() -> None:
    from planka_checker.client import PlankaClient

    fake_client = MagicMock()
    fake_client._request = AsyncMock(
        side_effect=lambda method, path, **kw: {
            "items": [],
        }
        if path.endswith("/actions")
        else None
    )

    client = PlankaClient(_settings())
    client._request = fake_client._request
    result = await client.get_card_activity(123, comments_total=0)

    actions_calls = [
        c
        for c in fake_client._request.await_args_list
        if c.args[1].endswith("/actions")
    ]
    comments_calls = [
        c
        for c in fake_client._request.await_args_list
        if c.args[1].endswith("/comments")
    ]
    assert len(actions_calls) == 1
    assert comments_calls == []
    assert result == []
