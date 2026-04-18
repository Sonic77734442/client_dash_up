from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Set
from uuid import UUID

from fastapi import HTTPException


@dataclass
class RequestContext:
    user_id: UUID
    role: str
    global_access: bool
    accessible_client_ids: Set[UUID]



def ensure_admin(ctx: RequestContext) -> None:
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Admin access required"})


def ensure_client_access(ctx: RequestContext, client_id: UUID) -> None:
    if ctx.global_access:
        return
    if client_id not in ctx.accessible_client_ids:
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Tenant access denied", "details": {"client_id": str(client_id)}},
        )


def ensure_account_access(ctx: RequestContext, account_client_id: UUID, account_id: Optional[UUID] = None) -> None:
    if ctx.global_access:
        return
    if account_client_id not in ctx.accessible_client_ids:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden",
                "message": "Account tenant access denied",
                "details": {
                    "account_id": str(account_id) if account_id else None,
                    "client_id": str(account_client_id),
                },
            },
        )
