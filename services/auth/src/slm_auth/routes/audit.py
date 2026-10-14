from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from slm_auth.deps import Pool
from slm_common.auth import Principal
from slm_common.db import tenant_tx
from slm_common.errors import ApiError
from slm_common.pagination import PageParams, build_page
from slm_common.rbac import Role, require_role

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    org_id: UUID | None
    actor_id: UUID | None
    actor_type: str
    action: str
    resource: str
    resource_id: str | None
    ip: str | None
    request_id: str | None
    detail: dict[str, Any]
    created_at: datetime


@router.get("")
async def list_audit_logs(
    pool: Pool,
    page: PageParams = Depends(),
    action: str | None = Query(None, max_length=128, description="Exact action, or a prefix ending in '.'"),
    resource: str | None = Query(None, max_length=64),
    actor_id: UUID | None = None,
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    principal: Principal = require_role(Role.org_admin),
):
    """Newest first, scoped to the caller's organization by row-level security."""
    org_id = principal.require_org()
    args: list[Any] = [org_id]
    where = ["org_id = $1"]

    def add(clause: str, value: Any) -> None:
        args.append(value)
        where.append(clause.format(n=len(args)))

    if action:
        if action.endswith("."):
            add("action LIKE ${n}", action.replace("%", r"\%").replace("_", r"\_") + "%")
        else:
            add("action = ${n}", action)
    if resource:
        add("resource = ${n}", resource)
    if actor_id:
        add("actor_id = ${n}", actor_id)
    if start:
        add("created_at >= ${n}", start)
    if end:
        add("created_at < ${n}", end)
    if page.cursor:
        try:
            add("id < ${n}", int(page.cursor["id"]))
        except (KeyError, ValueError) as exc:
            raise ApiError.bad_request("Invalid pagination cursor", "invalid_cursor") from exc
    args.append(page.fetch)
    sql = (
        "SELECT id, org_id, actor_id, actor_type, action, resource, resource_id, host(ip) AS ip, "
        f"request_id, detail, created_at FROM audit_logs WHERE {' AND '.join(where)} "
        f"ORDER BY id DESC LIMIT ${len(args)}"
    )
    async with tenant_tx(pool, org_id) as conn:
        rows = await conn.fetch(sql, *args)
    result = build_page(rows, page.limit, lambda r: {"id": r["id"]})
    result["items"] = [AuditEntry.model_validate(dict(r)) for r in result["items"]]
    return result
