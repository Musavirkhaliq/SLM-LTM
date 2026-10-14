from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel

from slm_common.errors import ApiError

T = TypeVar("T")
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode_cursor(payload: Mapping[str, Any]) -> str:
    """Opaque URL-safe cursor; datetimes/UUIDs are stringified (see `parse_ts` / `UUID`)."""
    raw = json.dumps(dict(payload), default=_json_default, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ApiError.bad_request("Invalid pagination cursor", "invalid_cursor") from exc
    if not isinstance(value, dict):
        raise ApiError.bad_request("Invalid pagination cursor", "invalid_cursor")
    return value


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"cannot encode {type(value).__name__} in cursor")


def parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ApiError.bad_request("Invalid pagination cursor", "invalid_cursor") from exc


def parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ApiError.bad_request("Invalid pagination cursor", "invalid_cursor") from exc


class PageParams:
    """Dependency for `?limit=&cursor=`; fetch `limit + 1` rows and pass them to `build_page`."""

    def __init__(
        self,
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        cursor: str | None = Query(None, max_length=1024),
    ) -> None:
        self.limit = limit
        self.cursor = decode_cursor(cursor) if cursor else None

    @property
    def fetch(self) -> int:
        return self.limit + 1


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def build_page(
    rows: Sequence[T], limit: int, cursor_for: Callable[[T], Mapping[str, Any]]
) -> dict[str, Any]:
    """Trim the extra probe row and derive `next_cursor` from the last returned row."""
    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = encode_cursor(cursor_for(items[-1])) if has_more and items else None
    return {"items": items, "next_cursor": next_cursor}
