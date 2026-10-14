from __future__ import annotations

from enum import IntEnum
from typing import Any

from fastapi import Depends

from slm_common.errors import ApiError


class Role(IntEnum):
    viewer = 10
    developer = 20
    ml_engineer = 30
    org_admin = 40
    platform_admin = 50

    @classmethod
    def parse(cls, value: str | int | Role) -> Role:
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            return cls(value)
        try:
            return cls[value]
        except KeyError:
            raise ValueError(f"unknown role: {value!r}") from None


def require_role(min_role: Role | str) -> Any:
    """FastAPI dependency: the caller must hold at least `min_role`; yields the Principal."""
    from slm_common.auth import get_principal  # deferred: auth imports Role from this module

    needed = Role.parse(min_role)

    async def checker(principal: Any = Depends(get_principal)) -> Any:
        if principal.role < needed:
            raise ApiError.forbidden(f"Requires role {needed.name} or higher")
        return principal

    return Depends(checker)
