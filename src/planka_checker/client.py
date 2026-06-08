"""Thin HTTP client for the Planka REST API.

This bypasses the third-party ``plankapy`` library because the version
on PyPI is incompatible with the schema of Planka 2.x. The wrapper
exposes a small, intention-revealing API used by the report generator.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPSConnection
from typing import Any, Iterable

from planka_checker.config import PlankaSettings
from planka_checker.planka_models import (
    PlankaAction,
    PlankaBoard,
    PlankaBoardData,
    PlankaCard,
    PlankaLabel,
    PlankaList,
    PlankaTask,
    PlankaUser,
    set_action_cache,
)


def _epoch() -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


class PlankaAPIError(RuntimeError):
    """Raised when a Planka API call fails."""


class PlankaClient:
    """Minimal Planka HTTP client.

    Authenticates with username/email + password on first use and
    caches the bearer token for subsequent calls.
    """

    def __init__(self, settings: PlankaSettings) -> None:
        self._settings = settings
        self._token: str | None = None
        self._base_url = settings.planka_url.rstrip("/") + "/"
        # Reuse a single urllib opener for HTTP keep-alive (significant speedup
        # on Planka instances that require ~50+ sequential calls).
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(debuglevel=0),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def _authenticate(self) -> str:
        if self._token is not None:
            return self._token
        body = json.dumps(
            {
                "emailOrUsername": self._settings.planka_username,
                "password": self._settings.planka_password,
            }
        ).encode("utf-8")
        response = self._request("POST", "api/access-tokens", body=body, auth=False)
        if not isinstance(response, dict) or "item" not in response:
            raise PlankaAPIError("Unexpected auth response from Planka")
        token = response["item"]
        if not isinstance(token, str) or not token:
            raise PlankaAPIError("Planka returned an empty access token")
        self._token = token
        return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        auth: bool = True,
    ) -> Any:
        url = self._base_url + path.lstrip("/")
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Connection": "keep-alive",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self._authenticate()}"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(req) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise PlankaAPIError(
                f"Planka {method} {path} failed: {exc.code} {exc.reason}: {detail}"
            ) from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlankaAPIError(f"Non-JSON response from Planka {path}: {exc}") from exc

    def get_board(self, board_id: int) -> PlankaBoardData:
        """Fetch a board and all the records in its ``included`` payload."""
        response = self._request("GET", f"api/boards/{board_id}")
        if not isinstance(response, dict):
            raise PlankaAPIError(f"Unexpected board response for {board_id}")
        return self._parse_board_response(response)

    def get_boards(self, board_ids: Iterable[int]) -> list[PlankaBoardData]:
        """Fetch multiple boards. Failures on individual boards are skipped."""
        results: list[PlankaBoardData] = []
        for board_id in board_ids:
            try:
                results.append(self.get_board(board_id))
            except PlankaAPIError:
                continue
        return results

    def get_card_actions(self, card_id: int) -> list[PlankaAction]:
        """Fetch actions for a card (createCard, moveCard, completeTask, ...).

        Planka v2.x also stores text comments at a separate endpoint; use
        :meth:`get_card_comments` to retrieve those and merge the results
        client-side if both are needed.
        """
        response = self._request("GET", f"api/cards/{card_id}/actions")
        items = response.get("items", []) if isinstance(response, dict) else []
        actions = [PlankaAction.from_api(item) for item in items if isinstance(item, dict)]
        set_action_cache(card_id, actions)
        return actions

    def get_card_comments(self, card_id: int) -> list[PlankaAction]:
        """Fetch text comments for a card as synthetic ``commentCard`` actions.

        Planka v2.x stores comments at ``/api/cards/{id}/comments``. The payload
        already includes the ``userId``, so the synthesized action has the
        author attributed.
        """
        response = self._request("GET", f"api/cards/{card_id}/comments")
        items = response.get("items", []) if isinstance(response, dict) else []
        return [
            PlankaAction.from_comment(item, card_id=card_id)
            for item in items
            if isinstance(item, dict)
        ]

    def get_card_activity(self, card_id: int) -> list[PlankaAction]:
        """Return all activity for a card (actions + comments), most recent first."""
        actions = self.get_card_actions(card_id)
        comments = self.get_card_comments(card_id)
        merged = actions + comments
        merged.sort(key=lambda a: a.created_at or _epoch(), reverse=True)
        return merged
    def _parse_board_response(self, response: dict[str, Any]) -> PlankaBoardData:
        item = response.get("item") or {}
        included = response.get("included") or {}

        board = PlankaBoard.from_api(item, base_url=self._base_url)
        users = {u.id: u for u in (PlankaUser.from_api(u) for u in included.get("users", []))}
        lists = {l.id: l for l in (PlankaList.from_api(l) for l in included.get("lists", []))}
        labels = {l.id: l for l in (PlankaLabel.from_api(l) for l in included.get("labels", []))}
        tasks = {t.id: t for t in (PlankaTask.from_api(t) for t in included.get("tasks", []))}

        cards: dict[int, PlankaCard] = {}
        for raw in included.get("cards", []):
            card = PlankaCard.from_api(raw)
            cards[card.id] = card

        card_label_map: dict[int, list[int]] = {}
        for raw in included.get("cardLabels", []):
            if not isinstance(raw, dict):
                continue
            cid = int(raw.get("cardId") or 0)
            lid = int(raw.get("labelId") or 0)
            card_label_map.setdefault(cid, []).append(lid)
            if cid in cards:
                cards[cid].label_ids.append(lid)

        card_member_map: dict[int, list[int]] = {}
        for raw in included.get("cardMemberships", []):
            if not isinstance(raw, dict):
                continue
            cid = int(raw.get("cardId") or 0)
            uid = int(raw.get("userId") or 0)
            card_member_map.setdefault(cid, []).append(uid)
            if cid in cards:
                cards[cid].member_ids.append(uid)

        card_task_map: dict[int, list[int]] = {}
        for task in tasks.values():
            card_task_map.setdefault(task.card_id, []).append(task.id)
            if task.card_id in cards:
                cards[task.card_id].task_ids.append(task.id)

        return PlankaBoardData(
            board=board,
            users=users,
            lists=lists,
            labels=labels,
            cards=cards,
            tasks=tasks,
            card_label_map=card_label_map,
            card_member_map=card_member_map,
            card_task_map=card_task_map,
        )
