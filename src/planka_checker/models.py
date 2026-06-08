"""Pydantic models for Planka report data structures."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ActionType = Literal[
    "createCard",
    "moveCard",
    "commentCard",
    "completeTask",
    "uncompleteTask",
    "addMemberToCard",
    "removeMemberFromCard",
]
ReportPeriod = Literal["day", "week"]


class CommentInfo(BaseModel):
    """Summary of a comment/action on a card (no text body)."""

    model_config = ConfigDict(extra="ignore")

    action_id: int
    type: ActionType
    user_id: int
    user_name: str
    created_at: datetime


class CardSummary(BaseModel):
    """Full card info for reports - matches MCP response schema."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    url: str = Field(description="Direct link to card in Planka")
    board_id: int
    board_name: str
    board_url: str = Field(
        default="", description="Direct link to the board that contains the card"
    )
    list_id: int
    list_name: str
    due_date: datetime | None = None
    is_due_date_completed: bool = False
    is_completed: bool = Field(
        default=False, description="True if all tasks on the card are completed"
    )
    labels: list[str] = Field(default_factory=list)
    members: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    last_comment: CommentInfo | None = Field(
        default=None, description="Most recent commentCard action"
    )
    last_activity: CommentInfo | None = Field(
        default=None, description="Most recent action of any type"
    )
    days_since_last_activity: int | None = Field(
        default=None, description="Used for 'forgotten' detection"
    )


class ActionSummary(BaseModel):
    """Enriched action info for activity reports."""

    model_config = ConfigDict(extra="ignore")

    id: int
    type: ActionType
    card_id: int
    card_name: str
    card_url: str
    user_id: int
    user_name: str
    board_id: int
    board_name: str
    board_url: str = Field(
        default="", description="Direct link to the board where the action occurred"
    )
    created_at: datetime
    extra_data: dict = Field(default_factory=dict)
    has_comment_text: bool = Field(
        default=False, description="Whether the action has a comment body (commentCard)"
    )


class ReportMetadata(BaseModel):
    """Report generation metadata."""

    model_config = ConfigDict(extra="ignore")

    generated_at: datetime
    period: ReportPeriod
    board_ids: list[int]
    boards_count: int
    actions_count: int
    overdue_count: int
    burning_count: int
    forgotten_count: int
    burning_hours: int
    forgotten_days: int


class PlankaReport(BaseModel):
    """Complete report structure - returned by all MCP tools."""

    model_config = ConfigDict(extra="ignore")

    metadata: ReportMetadata
    actions: list[ActionSummary] = Field(
        default_factory=list,
        description="All card actions that occurred during the report period",
    )
    overdue_cards: list[CardSummary] = Field(
        default_factory=list,
        description="Cards whose due date has passed and are not yet completed",
    )
    burning_cards: list[CardSummary] = Field(
        default_factory=list,
        description="Cards due within the burning_hours window",
    )
    forgotten_cards: list[CardSummary] = Field(
        default_factory=list,
        description=(
            "Overdue cards with no activity for forgotten_days or more"
        ),
    )
