from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

import hashlib
from threading import RLock
from typing import Dict, List, Optional, Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.db import init_sqlite, sqlite_conn
from app.schemas import (
    AgencyClientAccessCreate,
    AgencyClientAccessOut,
    AgencyCreate,
    AgencyInviteAcceptRequest,
    AgencyInviteAcceptResponse,
    AgencyInviteCreate,
    AgencyInviteIssueResponse,
    AgencyInviteOut,
    AgencyInviteResendRequest,
    AgencyMemberCreate,
    AgencyMemberOut,
    AgencyOut,
    AgencyPatch,
    UserClientAccessCreate,
    UserCreate,
    UserPatch,
    SessionIssueRequest,
)
from app.services.auth_arch import AuthStore


class PlatformAdminStore(Protocol):
    def create_agency(self, payload: AgencyCreate) -> AgencyOut: ...
    def list_agencies(self, *, status: str = "all") -> List[AgencyOut]: ...
    def get_agency(self, agency_id: UUID) -> Optional[AgencyOut]: ...
    def patch_agency(self, agency_id: UUID, payload: AgencyPatch) -> AgencyOut: ...
    def delete_agency(self, agency_id: UUID) -> None: ...
    def upsert_member(
        self,
        agency_id: UUID,
        payload: AgencyMemberCreate,
        *,
        actor_user_id: Optional[UUID] = None,
        actor_is_platform_admin: bool = False,
    ) -> AgencyMemberOut: ...
    def list_members(self, agency_id: UUID) -> List[AgencyMemberOut]: ...
    def assign_client(self, agency_id: UUID, payload: AgencyClientAccessCreate) -> AgencyClientAccessOut: ...
    def list_clients(self, agency_id: UUID) -> List[AgencyClientAccessOut]: ...
    def issue_invite(
        self, agency_id: UUID, payload: AgencyInviteCreate, *, invited_by: Optional[UUID], frontend_base_url: str
    ) -> AgencyInviteIssueResponse: ...
    def list_invites(self, agency_id: UUID, *, status: str = "all") -> List[AgencyInviteOut]: ...
    def revoke_invite(self, agency_id: UUID, invite_id: UUID) -> AgencyInviteOut: ...
    def resend_invite(
        self,
        agency_id: UUID,
        invite_id: UUID,
        payload: AgencyInviteResendRequest,
        *,
        invited_by: Optional[UUID],
        frontend_base_url: str,
    ) -> AgencyInviteIssueResponse: ...
    def deactivate_member(
        self,
        agency_id: UUID,
        member_id: UUID,
        *,
        actor_user_id: UUID,
        actor_is_platform_admin: bool = False,
    ) -> AgencyMemberOut: ...
    def remove_member(
        self,
        agency_id: UUID,
        member_id: UUID,
        *,
        actor_user_id: UUID,
        actor_is_platform_admin: bool = False,
    ) -> None: ...
    def revoke_client(self, agency_id: UUID, access_id: UUID) -> None: ...
    def accept_invite(
        self,
        payload: AgencyInviteAcceptRequest,
        *,
        session_ttl_minutes: int,
        accepting_user_id: Optional[UUID] = None,
    ) -> AgencyInviteAcceptResponse: ...


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or f"agency-{uuid4().hex[:8]}"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _raise_owner_protected() -> None:
    raise HTTPException(
        status_code=403,
        detail={
            "code": "agency_owner_protected",
            "message": "A manager cannot change, deactivate, or remove an agency owner",
        },
    )


def _raise_last_active_owner() -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "code": "last_active_agency_owner",
            "message": "Transfer ownership to another active user before deactivating, demoting, or removing this owner",
        },
    )


class SqlitePlatformAdminStore:
    def __init__(self, db_path: str, auth_store: AuthStore):
        self.db_path = db_path
        self.auth_store = auth_store
        init_sqlite(db_path)

    @staticmethod
    def _to_agency(row) -> AgencyOut:
        return AgencyOut(
            id=UUID(row["id"]),
            name=row["name"],
            slug=row["slug"],
            status=row["status"],
            plan=row["plan"],
            notes=row["notes"],
            allow_client_invites=bool(row["allow_client_invites"]) if "allow_client_invites" in row.keys() else True,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _to_member(row) -> AgencyMemberOut:
        return AgencyMemberOut(
            id=UUID(row["id"]),
            agency_id=UUID(row["agency_id"]),
            user_id=UUID(row["user_id"]),
            role=row["role"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _to_client_access(row) -> AgencyClientAccessOut:
        return AgencyClientAccessOut(
            id=UUID(row["id"]),
            agency_id=UUID(row["agency_id"]),
            client_id=UUID(row["client_id"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _to_invite(row) -> AgencyInviteOut:
        return AgencyInviteOut(
            id=UUID(row["id"]),
            agency_id=UUID(row["agency_id"]),
            email=row["email"],
            member_role=row["member_role"],
            status=row["status"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
            invited_by=UUID(row["invited_by"]) if row["invited_by"] else None,
            accepted_user_id=UUID(row["accepted_user_id"]) if row["accepted_user_id"] else None,
            accepted_at=datetime.fromisoformat(row["accepted_at"]) if row["accepted_at"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _derive_name_from_email(email: str) -> str:
        local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
        return local.title() or "Agency User"

    def _agency_or_404(self, conn, agency_id: UUID):
        row = conn.execute("SELECT * FROM agencies WHERE id=?", (str(agency_id),)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Agency not found")
        return row

    @staticmethod
    def _actor_member_role(
        conn,
        agency_id: UUID,
        *,
        actor_user_id: Optional[UUID],
        actor_is_platform_admin: bool,
        require_actor: bool,
    ) -> Optional[str]:
        # Platform administrators have an explicit emergency override for the
        # owner-vs-manager hierarchy. The last-active-owner invariant below is
        # deliberately never bypassed, including by platform administrators.
        if actor_is_platform_admin:
            return "platform_admin"
        if actor_user_id is None:
            if require_actor:
                raise HTTPException(status_code=403, detail="Agency member actor context is required")
            return None
        actor = conn.execute(
            """
            SELECT role
            FROM agency_members
            WHERE agency_id=? AND user_id=? AND status='active'
            """,
            (str(agency_id), str(actor_user_id)),
        ).fetchone()
        if not actor or actor["role"] not in {"owner", "manager"}:
            raise HTTPException(status_code=403, detail="Agency management access denied")
        return str(actor["role"])

    @staticmethod
    def _active_owner_count(conn, agency_id: UUID) -> int:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM agency_members m
            JOIN users u ON u.id=m.user_id
            WHERE m.agency_id=?
              AND m.role='owner'
              AND m.status='active'
              AND u.status='active'
              AND u.role IN ('agency', 'admin')
            """,
            (str(agency_id),),
        ).fetchone()
        return int(row["total"])

    def _assert_member_mutation_allowed(
        self,
        conn,
        agency_id: UUID,
        target,
        *,
        next_role: Optional[str],
        next_status: Optional[str],
        actor_user_id: Optional[UUID],
        actor_is_platform_admin: bool,
        require_actor: bool,
    ) -> None:
        actor_role = self._actor_member_role(
            conn,
            agency_id,
            actor_user_id=actor_user_id,
            actor_is_platform_admin=actor_is_platform_admin,
            require_actor=require_actor,
        )
        if actor_role == "manager" and (
            (target is not None and target["role"] == "owner") or next_role == "owner"
        ):
            _raise_owner_protected()

        if target is None or target["role"] != "owner" or target["status"] != "active":
            return
        remains_active_owner = next_role == "owner" and next_status == "active"
        if remains_active_owner:
            return
        target_user = conn.execute(
            "SELECT role, status FROM users WHERE id=?",
            (target["user_id"],),
        ).fetchone()
        target_is_active = bool(
            target_user
            and target_user["status"] == "active"
            and target_user["role"] in {"agency", "admin"}
        )
        if target_is_active and self._active_owner_count(conn, agency_id) <= 1:
            _raise_last_active_owner()

    def _sync_user_access_for_agency_client(self, conn, agency_id: UUID, client_id: UUID) -> None:
        now = _utcnow().isoformat()
        member_rows = conn.execute(
            """
            SELECT m.user_id
            FROM agency_members m
            JOIN users u ON u.id = m.user_id
            JOIN agencies a ON a.id = m.agency_id
            WHERE m.agency_id=?
              AND m.status='active'
              AND u.status='active'
              AND u.role='agency'
              AND a.status='active'
            """,
            (str(agency_id),),
        ).fetchall()
        for row in member_rows:
            access_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO user_client_access (id, user_id, client_id, role, created_at, updated_at)
                VALUES (?, ?, ?, 'agency', ?, ?)
                ON CONFLICT(user_id, client_id)
                DO UPDATE SET role='agency', updated_at=excluded.updated_at
                """,
                (access_id, row["user_id"], str(client_id), now, now),
            )

    def _sync_user_access_for_new_member(self, conn, agency_id: UUID, user_id: UUID) -> None:
        now = _utcnow().isoformat()
        client_rows = conn.execute(
            """
            SELECT DISTINCT aca.client_id
            FROM agency_client_access aca
            JOIN agencies a ON a.id = aca.agency_id
            JOIN agency_members am
              ON am.agency_id = aca.agency_id AND am.user_id = ?
            JOIN users u ON u.id = am.user_id
            WHERE aca.agency_id=?
              AND a.status='active'
              AND am.status='active'
              AND u.status='active'
              AND u.role='agency'
            """,
            (str(user_id), str(agency_id)),
        ).fetchall()
        for row in client_rows:
            access_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO user_client_access (id, user_id, client_id, role, created_at, updated_at)
                VALUES (?, ?, ?, 'agency', ?, ?)
                ON CONFLICT(user_id, client_id)
                DO UPDATE SET role='agency', updated_at=excluded.updated_at
                """,
                (access_id, str(user_id), row["client_id"], now, now),
            )

    def _rebuild_user_agency_access(self, conn, user_id: UUID) -> None:
        now = _utcnow().isoformat()
        user = conn.execute("SELECT role, status FROM users WHERE id=?", (str(user_id),)).fetchone()
        if not user:
            return
        if user["role"] == "agency":
            conn.execute("DELETE FROM user_client_access WHERE user_id=?", (str(user_id),))
        elif user["role"] == "admin":
            conn.execute("DELETE FROM user_client_access WHERE user_id=?", (str(user_id),))
            return
        else:
            # A stale membership must not erase an independent direct grant
            # after an agency user has been demoted to client/solo_client.
            conn.execute(
                "DELETE FROM user_client_access WHERE user_id=? AND role='agency'",
                (str(user_id),),
            )
            return
        if user["status"] != "active":
            return
        rows = conn.execute(
            """
            SELECT DISTINCT aca.client_id
            FROM agency_members am
            JOIN agencies a ON a.id = am.agency_id
            JOIN users u ON u.id = am.user_id
            JOIN agency_client_access aca ON aca.agency_id = am.agency_id
            WHERE am.user_id=?
              AND am.status='active'
              AND a.status='active'
              AND u.status='active'
              AND u.role='agency'
            """,
            (str(user_id),),
        ).fetchall()
        for row in rows:
            access_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO user_client_access (id, user_id, client_id, role, created_at, updated_at)
                VALUES (?, ?, ?, 'agency', ?, ?)
                ON CONFLICT(user_id, client_id)
                DO UPDATE SET role='agency', updated_at=excluded.updated_at
                """,
                (access_id, str(user_id), row["client_id"], now, now),
            )

    def create_agency(self, payload: AgencyCreate) -> AgencyOut:
        now = _utcnow().isoformat()
        agency_id = str(uuid4())
        slug = _slugify(payload.slug or payload.name)
        with sqlite_conn(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO agencies (id, name, slug, status, plan, notes, allow_client_invites, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        agency_id,
                        payload.name,
                        slug,
                        payload.status,
                        payload.plan,
                        payload.notes,
                        1 if payload.allow_client_invites else 0,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"Agency conflict: {exc}")
            row = conn.execute("SELECT * FROM agencies WHERE id=?", (agency_id,)).fetchone()
        return self._to_agency(row)

    def list_agencies(self, *, status: str = "all") -> List[AgencyOut]:
        where = "WHERE status=?" if status != "all" else ""
        params = (status,) if status != "all" else ()
        with sqlite_conn(self.db_path) as conn:
            rows = conn.execute(f"SELECT * FROM agencies {where} ORDER BY updated_at DESC", params).fetchall()
        return [self._to_agency(r) for r in rows]

    def get_agency(self, agency_id: UUID) -> Optional[AgencyOut]:
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute("SELECT * FROM agencies WHERE id=?", (str(agency_id),)).fetchone()
        return self._to_agency(row) if row else None

    def patch_agency(self, agency_id: UUID, payload: AgencyPatch) -> AgencyOut:
        patch = payload.model_dump(exclude_unset=True)
        with sqlite_conn(self.db_path) as conn:
            existing = self._agency_or_404(conn, agency_id)
            if not patch:
                return self._to_agency(existing)
            now = _utcnow().isoformat()
            data = {
                "name": patch.get("name", existing["name"]),
                "slug": _slugify(patch.get("slug", existing["slug"])),
                "status": patch.get("status", existing["status"]),
                "plan": patch.get("plan", existing["plan"]),
                "notes": patch.get("notes", existing["notes"]),
                "allow_client_invites": (
                    (1 if patch["allow_client_invites"] else 0)
                    if "allow_client_invites" in patch
                    else existing["allow_client_invites"]
                ),
            }
            try:
                conn.execute(
                    """
                    UPDATE agencies
                    SET name=?, slug=?, status=?, plan=?, notes=?, allow_client_invites=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        data["name"],
                        data["slug"],
                        data["status"],
                        data["plan"],
                        data["notes"],
                        data["allow_client_invites"],
                        now,
                        str(agency_id),
                    ),
                )
                if data["status"] != existing["status"]:
                    member_rows = conn.execute(
                        "SELECT DISTINCT user_id FROM agency_members WHERE agency_id=?",
                        (str(agency_id),),
                    ).fetchall()
                    for member_row in member_rows:
                        self._rebuild_user_agency_access(conn, UUID(member_row["user_id"]))
                conn.commit()
            except Exception as exc:
                raise HTTPException(status_code=409, detail=f"Agency conflict: {exc}")
            row = conn.execute("SELECT * FROM agencies WHERE id=?", (str(agency_id),)).fetchone()
        return self._to_agency(row)

    def _should_demote_agency_user(self, conn, user_id: UUID) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM agency_members am
            JOIN agencies a ON a.id = am.agency_id
            WHERE am.user_id=?
              AND am.status='active'
              AND a.status='active'
            LIMIT 1
            """,
            (str(user_id),),
        ).fetchone()
        return row is None

    def delete_agency(self, agency_id: UUID) -> None:
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            member_rows = conn.execute(
                "SELECT DISTINCT user_id FROM agency_members WHERE agency_id=?",
                (str(agency_id),),
            ).fetchall()
            affected_user_ids = [UUID(r["user_id"]) for r in member_rows]

            conn.execute("DELETE FROM agency_invites WHERE agency_id=?", (str(agency_id),))
            conn.execute("DELETE FROM agency_client_access WHERE agency_id=?", (str(agency_id),))
            conn.execute("DELETE FROM agency_members WHERE agency_id=?", (str(agency_id),))
            conn.execute(
                "DELETE FROM integration_credentials WHERE scope_type='agency' AND scope_id=?",
                (str(agency_id),),
            )
            conn.execute("DELETE FROM agencies WHERE id=?", (str(agency_id),))

            for user_id in affected_user_ids:
                self._rebuild_user_agency_access(conn, user_id)
                user_row = conn.execute("SELECT role FROM users WHERE id=?", (str(user_id),)).fetchone()
                if user_row and user_row["role"] == "agency" and self._should_demote_agency_user(conn, user_id):
                    conn.execute(
                        "UPDATE users SET role='client', updated_at=? WHERE id=?",
                        (now, str(user_id)),
                    )

            conn.commit()

    def upsert_member(
        self,
        agency_id: UUID,
        payload: AgencyMemberCreate,
        *,
        actor_user_id: Optional[UUID] = None,
        actor_is_platform_admin: bool = False,
    ) -> AgencyMemberOut:
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            # Serialize the owner-count check with the mutation. Without an
            # immediate write lock, two requests could both observe two owners
            # and remove/demote one each.
            conn.execute("BEGIN IMMEDIATE")
            self._agency_or_404(conn, agency_id)
            user = conn.execute("SELECT * FROM users WHERE id=?", (str(payload.user_id),)).fetchone()
            if not user:
                raise HTTPException(status_code=400, detail="user_id does not exist")
            if user["role"] not in {"agency", "admin"}:
                raise HTTPException(status_code=400, detail="agency members must have role agency or admin")

            existing = conn.execute(
                "SELECT * FROM agency_members WHERE agency_id=? AND user_id=?",
                (str(agency_id), str(payload.user_id)),
            ).fetchone()
            self._assert_member_mutation_allowed(
                conn,
                agency_id,
                existing,
                next_role=payload.role,
                next_status=payload.status,
                actor_user_id=actor_user_id,
                actor_is_platform_admin=actor_is_platform_admin,
                require_actor=existing is not None,
            )
            if existing:
                conn.execute(
                    """
                    UPDATE agency_members
                    SET role=?, status=?, updated_at=?
                    WHERE id=?
                    """,
                    (payload.role, payload.status, now, existing["id"]),
                )
                member_id = existing["id"]
            else:
                member_id = str(uuid4())
                conn.execute(
                    """
                    INSERT INTO agency_members (id, agency_id, user_id, role, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (member_id, str(agency_id), str(payload.user_id), payload.role, payload.status, now, now),
                )

            self._rebuild_user_agency_access(conn, payload.user_id)

            conn.commit()
            row = conn.execute("SELECT * FROM agency_members WHERE id=?", (member_id,)).fetchone()
        return self._to_member(row)

    def list_members(self, agency_id: UUID) -> List[AgencyMemberOut]:
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            rows = conn.execute(
                "SELECT * FROM agency_members WHERE agency_id=? ORDER BY updated_at DESC",
                (str(agency_id),),
            ).fetchall()
        return [self._to_member(r) for r in rows]

    def assign_client(self, agency_id: UUID, payload: AgencyClientAccessCreate) -> AgencyClientAccessOut:
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            client = conn.execute("SELECT id FROM clients WHERE id=?", (str(payload.client_id),)).fetchone()
            if not client:
                raise HTTPException(status_code=400, detail="client_id does not exist")

            existing = conn.execute(
                "SELECT * FROM agency_client_access WHERE agency_id=? AND client_id=?",
                (str(agency_id), str(payload.client_id)),
            ).fetchone()
            if existing:
                conn.execute("UPDATE agency_client_access SET updated_at=? WHERE id=?", (now, existing["id"]))
                access_id = existing["id"]
            else:
                access_id = str(uuid4())
                conn.execute(
                    """
                    INSERT INTO agency_client_access (id, agency_id, client_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (access_id, str(agency_id), str(payload.client_id), now, now),
                )

            affected_users = conn.execute(
                "SELECT user_id FROM agency_members WHERE agency_id=?",
                (str(agency_id),),
            ).fetchall()
            for affected in affected_users:
                self._rebuild_user_agency_access(conn, UUID(affected["user_id"]))
            conn.commit()
            row = conn.execute("SELECT * FROM agency_client_access WHERE id=?", (access_id,)).fetchone()
        return self._to_client_access(row)

    def list_clients(self, agency_id: UUID) -> List[AgencyClientAccessOut]:
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            rows = conn.execute(
                "SELECT * FROM agency_client_access WHERE agency_id=? ORDER BY updated_at DESC",
                (str(agency_id),),
            ).fetchall()
        return [self._to_client_access(r) for r in rows]

    def issue_invite(
        self, agency_id: UUID, payload: AgencyInviteCreate, *, invited_by: Optional[UUID], frontend_base_url: str
    ) -> AgencyInviteIssueResponse:
        now = _utcnow()
        expires_at = now + timedelta(days=payload.expires_in_days)
        invite_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        token_hash = _token_hash(token)
        email = payload.email.strip().lower()
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            conn.execute(
                """
                UPDATE agency_invites
                SET status='revoked', updated_at=?
                WHERE agency_id=? AND lower(email)=lower(?) AND status='pending'
                """,
                (now.isoformat(), str(agency_id), email),
            )
            conn.execute(
                """
                INSERT INTO agency_invites
                (id, agency_id, email, member_role, token_hash, status, expires_at, invited_by, accepted_user_id, accepted_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    invite_id,
                    str(agency_id),
                    email,
                    payload.member_role,
                    token_hash,
                    expires_at.isoformat(),
                    str(invited_by) if invited_by else None,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM agency_invites WHERE id=?", (invite_id,)).fetchone()
        base = frontend_base_url.rstrip("/")
        accept_url = f"{base}/login?invite_token={token}"
        return AgencyInviteIssueResponse(invite=self._to_invite(row), invite_token=token, accept_url=accept_url)

    def list_invites(self, agency_id: UUID, *, status: str = "all") -> List[AgencyInviteOut]:
        where = "WHERE agency_id=?"
        params: List[object] = [str(agency_id)]
        if status != "all":
            where += " AND status=?"
            params.append(status)
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            rows = conn.execute(
                f"SELECT * FROM agency_invites {where} ORDER BY updated_at DESC",
                tuple(params),
            ).fetchall()
        return [self._to_invite(r) for r in rows]

    def revoke_invite(self, agency_id: UUID, invite_id: UUID) -> AgencyInviteOut:
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            row = conn.execute(
                "SELECT * FROM agency_invites WHERE id=? AND agency_id=?",
                (str(invite_id), str(agency_id)),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Invite not found")
            if row["status"] in {"accepted", "expired"}:
                raise HTTPException(status_code=409, detail="Invite cannot be revoked in current status")
            cursor = conn.execute(
                "UPDATE agency_invites SET status='revoked', updated_at=? WHERE id=? AND status='pending'",
                (now, str(invite_id)),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM agency_invites WHERE id=?", (str(invite_id),)).fetchone()
            if cursor.rowcount != 1 and updated["status"] != "revoked":
                raise HTTPException(status_code=409, detail="Invite changed while it was being revoked")
        return self._to_invite(updated)

    def resend_invite(
        self,
        agency_id: UUID,
        invite_id: UUID,
        payload: AgencyInviteResendRequest,
        *,
        invited_by: Optional[UUID],
        frontend_base_url: str,
    ) -> AgencyInviteIssueResponse:
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            row = conn.execute(
                "SELECT * FROM agency_invites WHERE id=? AND agency_id=?",
                (str(invite_id), str(agency_id)),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Invite not found")
            if row["status"] == "pending":
                conn.execute(
                    "UPDATE agency_invites SET status='revoked', updated_at=? WHERE id=?",
                    (_utcnow().isoformat(), str(invite_id)),
                )
                conn.commit()
        return self.issue_invite(
            agency_id,
            AgencyInviteCreate(
                email=row["email"],
                member_role=row["member_role"],
                expires_in_days=payload.expires_in_days,
            ),
            invited_by=invited_by,
            frontend_base_url=frontend_base_url,
        )

    def deactivate_member(
        self,
        agency_id: UUID,
        member_id: UUID,
        *,
        actor_user_id: UUID,
        actor_is_platform_admin: bool = False,
    ) -> AgencyMemberOut:
        now = _utcnow().isoformat()
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._agency_or_404(conn, agency_id)
            row = conn.execute(
                "SELECT * FROM agency_members WHERE id=? AND agency_id=?",
                (str(member_id), str(agency_id)),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found")
            self._assert_member_mutation_allowed(
                conn,
                agency_id,
                row,
                next_role=row["role"],
                next_status="inactive",
                actor_user_id=actor_user_id,
                actor_is_platform_admin=actor_is_platform_admin,
                require_actor=True,
            )
            conn.execute(
                "UPDATE agency_members SET status='inactive', updated_at=? WHERE id=?",
                (now, str(member_id)),
            )
            self._rebuild_user_agency_access(conn, UUID(row["user_id"]))
            conn.commit()
            updated = conn.execute("SELECT * FROM agency_members WHERE id=?", (str(member_id),)).fetchone()
        return self._to_member(updated)

    def remove_member(
        self,
        agency_id: UUID,
        member_id: UUID,
        *,
        actor_user_id: UUID,
        actor_is_platform_admin: bool = False,
    ) -> None:
        with sqlite_conn(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._agency_or_404(conn, agency_id)
            row = conn.execute(
                "SELECT * FROM agency_members WHERE id=? AND agency_id=?",
                (str(member_id), str(agency_id)),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found")
            self._assert_member_mutation_allowed(
                conn,
                agency_id,
                row,
                next_role=None,
                next_status=None,
                actor_user_id=actor_user_id,
                actor_is_platform_admin=actor_is_platform_admin,
                require_actor=True,
            )
            user_id = UUID(row["user_id"])
            conn.execute("DELETE FROM agency_members WHERE id=?", (str(member_id),))
            self._rebuild_user_agency_access(conn, user_id)
            conn.commit()

    def revoke_client(self, agency_id: UUID, access_id: UUID) -> None:
        with sqlite_conn(self.db_path) as conn:
            self._agency_or_404(conn, agency_id)
            row = conn.execute(
                "SELECT * FROM agency_client_access WHERE id=? AND agency_id=?",
                (str(access_id), str(agency_id)),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Client access binding not found")
            conn.execute("DELETE FROM agency_client_access WHERE id=?", (str(access_id),))
            members = conn.execute(
                "SELECT user_id FROM agency_members WHERE agency_id=?",
                (str(agency_id),),
            ).fetchall()
            for m in members:
                self._rebuild_user_agency_access(conn, UUID(m["user_id"]))
            conn.commit()

    def accept_invite(
        self,
        payload: AgencyInviteAcceptRequest,
        *,
        session_ttl_minutes: int,
        accepting_user_id: Optional[UUID] = None,
    ) -> AgencyInviteAcceptResponse:
        now = _utcnow()
        token_hash = _token_hash(payload.token.strip())
        created_user = False
        with sqlite_conn(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM agency_invites WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail={"code": "invite_not_found", "message": "Invite token is invalid"})
            if row["status"] != "pending":
                raise HTTPException(status_code=409, detail={"code": "invite_not_pending", "message": "Invite is no longer pending"})
            if datetime.fromisoformat(row["expires_at"]) < now:
                conn.execute(
                    "UPDATE agency_invites SET status='expired', updated_at=? WHERE id=?",
                    (now.isoformat(), row["id"]),
                )
                conn.commit()
                raise HTTPException(status_code=400, detail={"code": "invite_expired", "message": "Invite is expired"})

            agency = self._agency_or_404(conn, UUID(row["agency_id"]))
            if agency["status"] != "active":
                raise HTTPException(status_code=409, detail={"code": "agency_inactive", "message": "Agency is not active"})

            user = self.auth_store.find_user_by_email(row["email"])
            if user:
                if user.role != "agency":
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "user_role_conflict", "message": "Existing user role cannot accept agency invite"},
                    )
                if user.status != "active":
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "existing_user_inactive", "message": "Existing user must be active to accept invite"},
                    )
                if accepting_user_id != user.id:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "existing_user_auth_required",
                            "message": "Sign in as the invited user before accepting this invite",
                        },
                    )
            else:
                if accepting_user_id is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "invite_identity_mismatch",
                            "message": "Sign out before accepting an invite for a new user",
                        },
                    )
                user = self.auth_store.create_user(
                    UserCreate(
                        email=row["email"],
                        name=(payload.name.strip() if payload.name else self._derive_name_from_email(row["email"])),
                        role="agency",
                        status="active",
                    )
                )
                created_user = True
                password = (payload.password or "").strip()
                if len(password) < 8:
                    password = f"{secrets.token_urlsafe(18)}Aa1"
                self.auth_store.set_password(user.id, password)

            existing_member = conn.execute(
                "SELECT * FROM agency_members WHERE agency_id=? AND user_id=?",
                (row["agency_id"], str(user.id)),
            ).fetchone()
            if existing_member:
                conn.execute(
                    """
                    UPDATE agency_members
                    SET role=?, status='active', updated_at=?
                    WHERE id=?
                    """,
                    (row["member_role"], now.isoformat(), existing_member["id"]),
                )
                member_id = existing_member["id"]
            else:
                member_id = str(uuid4())
                conn.execute(
                    """
                    INSERT INTO agency_members (id, agency_id, user_id, role, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (member_id, row["agency_id"], str(user.id), row["member_role"], now.isoformat(), now.isoformat()),
                )

            self._rebuild_user_agency_access(conn, user.id)
            accepted = conn.execute(
                """
                UPDATE agency_invites
                SET status='accepted', accepted_user_id=?, accepted_at=?, updated_at=?
                WHERE id=? AND status='pending'
                """,
                (str(user.id), now.isoformat(), now.isoformat(), row["id"]),
            )
            if accepted.rowcount != 1:
                current = conn.execute("SELECT status FROM agency_invites WHERE id=?", (row["id"],)).fetchone()
                conn.rollback()
                if created_user:
                    self.auth_store.delete_user(user.id)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "invite_not_pending",
                        "message": f"Invite is no longer pending ({current['status'] if current else 'missing'})",
                    },
                )
            conn.commit()
            updated = conn.execute("SELECT * FROM agency_invites WHERE id=?", (row["id"],)).fetchone()
            member_row = conn.execute("SELECT * FROM agency_members WHERE id=?", (member_id,)).fetchone()

        try:
            session = self.auth_store.issue_session(
                SessionIssueRequest(user_id=user.id, ttl_minutes=session_ttl_minutes)
            )
        except Exception as exc:
            with sqlite_conn(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    UPDATE agency_invites
                    SET status='pending', accepted_user_id=NULL, accepted_at=NULL, updated_at=?
                    WHERE id=? AND status='accepted' AND accepted_user_id=? AND accepted_at=?
                    """,
                    (now.isoformat(), row["id"], str(user.id), now.isoformat()),
                )
                if existing_member:
                    conn.execute(
                        """
                        UPDATE agency_members
                        SET role=?, status=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            existing_member["role"],
                            existing_member["status"],
                            existing_member["updated_at"],
                            existing_member["id"],
                        ),
                    )
                else:
                    conn.execute("DELETE FROM agency_members WHERE id=?", (member_id,))
                self._rebuild_user_agency_access(conn, user.id)
                conn.commit()
            if created_user:
                self.auth_store.delete_user(user.id)
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(
                status_code=503,
                detail={"code": "invite_provisioning_failed", "message": "Invite provisioning failed; retry safely"},
            ) from exc
        return AgencyInviteAcceptResponse(
            invite=self._to_invite(updated),
            agency=self._to_agency(agency),
            member=self._to_member(member_row),
            user=user,
            session=session,
        )


class InMemoryPlatformAdminStore:
    def __init__(self, auth_store: AuthStore):
        self.auth_store = auth_store
        self._member_lock = RLock()
        self.agencies: Dict[UUID, AgencyOut] = {}
        self.members: Dict[str, AgencyMemberOut] = {}
        self.clients: Dict[str, AgencyClientAccessOut] = {}
        self.invites: Dict[UUID, AgencyInviteOut] = {}
        self.invite_token_hash_to_id: Dict[str, UUID] = {}
        register_owner_guard = getattr(auth_store, "register_agency_owner_exit_guard", None)
        if callable(register_owner_guard):
            register_owner_guard(self._assert_user_can_exit_owner_role)
        register_access_rebuilder = getattr(auth_store, "register_agency_access_rebuilder", None)
        if callable(register_access_rebuilder):
            register_access_rebuilder(self._rebuild_user_agency_access)

    def _agency_or_404(self, agency_id: UUID) -> AgencyOut:
        agency = self.agencies.get(agency_id)
        if not agency:
            raise HTTPException(status_code=404, detail="Agency not found")
        return agency

    def _actor_member_role(
        self,
        agency_id: UUID,
        *,
        actor_user_id: Optional[UUID],
        actor_is_platform_admin: bool,
        require_actor: bool,
    ) -> Optional[str]:
        if actor_is_platform_admin:
            return "platform_admin"
        if actor_user_id is None:
            if require_actor:
                raise HTTPException(status_code=403, detail="Agency member actor context is required")
            return None
        actor = self.members.get(f"{agency_id}:{actor_user_id}")
        if not actor or actor.status != "active" or actor.role not in {"owner", "manager"}:
            raise HTTPException(status_code=403, detail="Agency management access denied")
        return actor.role

    def _active_owner_count(self, agency_id: UUID) -> int:
        total = 0
        for member in self.members.values():
            if member.agency_id != agency_id or member.role != "owner" or member.status != "active":
                continue
            user = self.auth_store.get_user(member.user_id)
            if user and user.status == "active" and user.role in {"agency", "admin"}:
                total += 1
        return total

    def _assert_user_can_exit_owner_role(self, user_id: UUID) -> None:
        with self._member_lock:
            owned_agency_ids = {
                member.agency_id
                for member in self.members.values()
                if member.user_id == user_id
                and member.role == "owner"
                and member.status == "active"
            }
            for agency_id in owned_agency_ids:
                if self._active_owner_count(agency_id) <= 1:
                    _raise_last_active_owner()

    def _assert_member_mutation_allowed(
        self,
        agency_id: UUID,
        target: Optional[AgencyMemberOut],
        *,
        next_role: Optional[str],
        next_status: Optional[str],
        actor_user_id: Optional[UUID],
        actor_is_platform_admin: bool,
        require_actor: bool,
    ) -> None:
        actor_role = self._actor_member_role(
            agency_id,
            actor_user_id=actor_user_id,
            actor_is_platform_admin=actor_is_platform_admin,
            require_actor=require_actor,
        )
        if actor_role == "manager" and (
            (target is not None and target.role == "owner") or next_role == "owner"
        ):
            _raise_owner_protected()

        if target is None or target.role != "owner" or target.status != "active":
            return
        remains_active_owner = next_role == "owner" and next_status == "active"
        if remains_active_owner:
            return
        target_user = self.auth_store.get_user(target.user_id)
        target_is_active = bool(
            target_user and target_user.status == "active" and target_user.role in {"agency", "admin"}
        )
        if target_is_active and self._active_owner_count(agency_id) <= 1:
            _raise_last_active_owner()

    def _sync_member_client_access(self, agency_id: UUID, user_id: UUID) -> None:
        agency = self.agencies.get(agency_id)
        user = self.auth_store.get_user(user_id)
        member = self.members.get(f"{agency_id}:{user_id}")
        if (
            not agency
            or agency.status != "active"
            or not user
            or user.status != "active"
            or user.role != "agency"
            or not member
            or member.status != "active"
        ):
            return
        for access in self.clients.values():
            if access.agency_id == agency_id:
                self.auth_store.assign_client_access(
                    UserClientAccessCreate(user_id=user_id, client_id=access.client_id, role="agency")
                )

    def _sync_client_members_access(self, agency_id: UUID, client_id: UUID) -> None:
        agency = self.agencies.get(agency_id)
        if not agency or agency.status != "active":
            return
        for member in self.members.values():
            user = self.auth_store.get_user(member.user_id)
            if (
                member.agency_id == agency_id
                and member.status == "active"
                and user is not None
                and user.status == "active"
                and user.role == "agency"
            ):
                self.auth_store.assign_client_access(
                    UserClientAccessCreate(user_id=member.user_id, client_id=client_id, role="agency")
                )

    def _rebuild_user_agency_access(self, user_id: UUID) -> None:
        if not hasattr(self.auth_store, "access"):
            return
        # InMemoryAuthStore keeps grants in .access dict with UserClientAccessOut values.
        access_dict = getattr(self.auth_store, "access", {})
        user = self.auth_store.get_user(user_id)
        if not user:
            return
        for key, value in list(access_dict.items()):
            if value.user_id != user_id:
                continue
            if user.role in {"agency", "admin"} or value.role == "agency":
                access_dict.pop(key, None)
        if user.role != "agency" or user.status != "active":
            return
        active_agencies = {a.id for a in self.agencies.values() if a.status == "active"}
        for member in self.members.values():
            if member.user_id != user_id or member.status != "active" or member.agency_id not in active_agencies:
                continue
            for binding in self.clients.values():
                if binding.agency_id == member.agency_id:
                    self.auth_store.assign_client_access(
                        UserClientAccessCreate(user_id=user_id, client_id=binding.client_id, role="agency")
                    )

    def create_agency(self, payload: AgencyCreate) -> AgencyOut:
        now = _utcnow()
        slug = _slugify(payload.slug or payload.name)
        if any(a.slug == slug for a in self.agencies.values()):
            raise HTTPException(status_code=409, detail="Agency conflict: duplicate slug")
        if any(a.name.lower() == payload.name.lower() for a in self.agencies.values()):
            raise HTTPException(status_code=409, detail="Agency conflict: duplicate name")
        rec = AgencyOut(
            id=uuid4(),
            name=payload.name,
            slug=slug,
            status=payload.status,
            plan=payload.plan,
            notes=payload.notes,
            allow_client_invites=payload.allow_client_invites,
            created_at=now,
            updated_at=now,
        )
        self.agencies[rec.id] = rec
        return rec

    def list_agencies(self, *, status: str = "all") -> List[AgencyOut]:
        rows = list(self.agencies.values())
        if status != "all":
            rows = [x for x in rows if x.status == status]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def get_agency(self, agency_id: UUID) -> Optional[AgencyOut]:
        return self.agencies.get(agency_id)

    def patch_agency(self, agency_id: UUID, payload: AgencyPatch) -> AgencyOut:
        existing = self._agency_or_404(agency_id)
        patch = payload.model_dump(exclude_unset=True)
        if not patch:
            return existing
        if "slug" in patch and patch["slug"]:
            patch["slug"] = _slugify(str(patch["slug"]))
        if "name" in patch and patch["name"]:
            for agency in self.agencies.values():
                if agency.id != agency_id and agency.name.lower() == str(patch["name"]).lower():
                    raise HTTPException(status_code=409, detail="Agency conflict: duplicate name")
        if "slug" in patch and patch["slug"]:
            for agency in self.agencies.values():
                if agency.id != agency_id and agency.slug == patch["slug"]:
                    raise HTTPException(status_code=409, detail="Agency conflict: duplicate slug")
        rec = existing.model_copy(update={**patch, "updated_at": _utcnow()})
        self.agencies[agency_id] = rec
        if rec.status != existing.status:
            affected_user_ids = {
                member.user_id for member in self.members.values() if member.agency_id == agency_id
            }
            for user_id in affected_user_ids:
                self._rebuild_user_agency_access(user_id)
        return rec

    def delete_agency(self, agency_id: UUID) -> None:
        self._agency_or_404(agency_id)
        affected_user_ids = {m.user_id for m in self.members.values() if m.agency_id == agency_id}

        for inv in [x for x in self.invites.values() if x.agency_id == agency_id]:
            self.invites.pop(inv.id, None)
        for key, invite_id in list(self.invite_token_hash_to_id.items()):
            if invite_id not in self.invites:
                self.invite_token_hash_to_id.pop(key, None)

        for key, binding in list(self.clients.items()):
            if binding.agency_id == agency_id:
                self.clients.pop(key, None)
        for key, member in list(self.members.items()):
            if member.agency_id == agency_id:
                self.members.pop(key, None)
        self.agencies.pop(agency_id, None)

        for user_id in affected_user_ids:
            self._rebuild_user_agency_access(user_id)
            user = self.auth_store.get_user(user_id)
            if not user or user.role != "agency":
                continue
            still_active_member = any(
                m.user_id == user_id
                and m.status == "active"
                and (self.agencies.get(m.agency_id) is not None)
                and self.agencies[m.agency_id].status == "active"
                for m in self.members.values()
            )
            if not still_active_member:
                self.auth_store.patch_user(user_id, UserPatch(role="client"))

    def upsert_member(
        self,
        agency_id: UUID,
        payload: AgencyMemberCreate,
        *,
        actor_user_id: Optional[UUID] = None,
        actor_is_platform_admin: bool = False,
    ) -> AgencyMemberOut:
        with self._member_lock:
            self._agency_or_404(agency_id)
            user = self.auth_store.get_user(payload.user_id)
            if not user:
                raise HTTPException(status_code=400, detail="user_id does not exist")
            if user.role not in {"agency", "admin"}:
                raise HTTPException(status_code=400, detail="agency members must have role agency or admin")

            key = f"{agency_id}:{payload.user_id}"
            now = _utcnow()
            existing = self.members.get(key)
            self._assert_member_mutation_allowed(
                agency_id,
                existing,
                next_role=payload.role,
                next_status=payload.status,
                actor_user_id=actor_user_id,
                actor_is_platform_admin=actor_is_platform_admin,
                require_actor=existing is not None,
            )
            if existing:
                rec = existing.model_copy(update={"role": payload.role, "status": payload.status, "updated_at": now})
            else:
                rec = AgencyMemberOut(
                    id=uuid4(),
                    agency_id=agency_id,
                    user_id=payload.user_id,
                    role=payload.role,
                    status=payload.status,
                    created_at=now,
                    updated_at=now,
                )
            self.members[key] = rec

            self._rebuild_user_agency_access(payload.user_id)
            return rec

    def list_members(self, agency_id: UUID) -> List[AgencyMemberOut]:
        with self._member_lock:
            self._agency_or_404(agency_id)
            rows = [x for x in self.members.values() if x.agency_id == agency_id]
            rows.sort(key=lambda x: x.updated_at, reverse=True)
            return rows

    def assign_client(self, agency_id: UUID, payload: AgencyClientAccessCreate) -> AgencyClientAccessOut:
        self._agency_or_404(agency_id)
        key = f"{agency_id}:{payload.client_id}"
        now = _utcnow()
        existing = self.clients.get(key)
        if existing:
            rec = existing.model_copy(update={"updated_at": now})
        else:
            rec = AgencyClientAccessOut(
                id=uuid4(),
                agency_id=agency_id,
                client_id=payload.client_id,
                created_at=now,
                updated_at=now,
            )
        self.clients[key] = rec
        affected_user_ids = {
            member.user_id for member in self.members.values() if member.agency_id == agency_id
        }
        for user_id in affected_user_ids:
            self._rebuild_user_agency_access(user_id)
        return rec

    def list_clients(self, agency_id: UUID) -> List[AgencyClientAccessOut]:
        self._agency_or_404(agency_id)
        rows = [x for x in self.clients.values() if x.agency_id == agency_id]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def issue_invite(
        self, agency_id: UUID, payload: AgencyInviteCreate, *, invited_by: Optional[UUID], frontend_base_url: str
    ) -> AgencyInviteIssueResponse:
        self._agency_or_404(agency_id)
        now = _utcnow()
        email = payload.email.strip().lower()
        for invite in list(self.invites.values()):
            if invite.agency_id == agency_id and invite.email.lower() == email and invite.status == "pending":
                self.invites[invite.id] = invite.model_copy(update={"status": "revoked", "updated_at": now})
        token = secrets.token_urlsafe(32)
        token_hash = _token_hash(token)
        invite = AgencyInviteOut(
            id=uuid4(),
            agency_id=agency_id,
            email=email,
            member_role=payload.member_role,
            status="pending",
            expires_at=now + timedelta(days=payload.expires_in_days),
            invited_by=invited_by,
            accepted_user_id=None,
            accepted_at=None,
            created_at=now,
            updated_at=now,
        )
        self.invites[invite.id] = invite
        self.invite_token_hash_to_id[token_hash] = invite.id
        base = frontend_base_url.rstrip("/")
        return AgencyInviteIssueResponse(invite=invite, invite_token=token, accept_url=f"{base}/login?invite_token={token}")

    def list_invites(self, agency_id: UUID, *, status: str = "all") -> List[AgencyInviteOut]:
        self._agency_or_404(agency_id)
        rows = [x for x in self.invites.values() if x.agency_id == agency_id]
        if status != "all":
            rows = [x for x in rows if x.status == status]
        rows.sort(key=lambda x: x.updated_at, reverse=True)
        return rows

    def revoke_invite(self, agency_id: UUID, invite_id: UUID) -> AgencyInviteOut:
        self._agency_or_404(agency_id)
        invite = self.invites.get(invite_id)
        if not invite or invite.agency_id != agency_id:
            raise HTTPException(status_code=404, detail="Invite not found")
        if invite.status in {"accepted", "expired"}:
            raise HTTPException(status_code=409, detail="Invite cannot be revoked in current status")
        updated = invite.model_copy(update={"status": "revoked", "updated_at": _utcnow()})
        self.invites[invite_id] = updated
        return updated

    def resend_invite(
        self,
        agency_id: UUID,
        invite_id: UUID,
        payload: AgencyInviteResendRequest,
        *,
        invited_by: Optional[UUID],
        frontend_base_url: str,
    ) -> AgencyInviteIssueResponse:
        self._agency_or_404(agency_id)
        invite = self.invites.get(invite_id)
        if not invite or invite.agency_id != agency_id:
            raise HTTPException(status_code=404, detail="Invite not found")
        if invite.status == "pending":
            self.invites[invite_id] = invite.model_copy(update={"status": "revoked", "updated_at": _utcnow()})
        return self.issue_invite(
            agency_id,
            AgencyInviteCreate(email=invite.email, member_role=invite.member_role, expires_in_days=payload.expires_in_days),
            invited_by=invited_by,
            frontend_base_url=frontend_base_url,
        )

    def deactivate_member(
        self,
        agency_id: UUID,
        member_id: UUID,
        *,
        actor_user_id: UUID,
        actor_is_platform_admin: bool = False,
    ) -> AgencyMemberOut:
        with self._member_lock:
            self._agency_or_404(agency_id)
            target = next(
                (m for m in self.members.values() if m.id == member_id and m.agency_id == agency_id),
                None,
            )
            if not target:
                raise HTTPException(status_code=404, detail="Member not found")
            self._assert_member_mutation_allowed(
                agency_id,
                target,
                next_role=target.role,
                next_status="inactive",
                actor_user_id=actor_user_id,
                actor_is_platform_admin=actor_is_platform_admin,
                require_actor=True,
            )
            updated = target.model_copy(update={"status": "inactive", "updated_at": _utcnow()})
            self.members[f"{agency_id}:{target.user_id}"] = updated
            self._rebuild_user_agency_access(target.user_id)
            return updated

    def remove_member(
        self,
        agency_id: UUID,
        member_id: UUID,
        *,
        actor_user_id: UUID,
        actor_is_platform_admin: bool = False,
    ) -> None:
        with self._member_lock:
            self._agency_or_404(agency_id)
            target = next(
                (m for m in self.members.values() if m.id == member_id and m.agency_id == agency_id),
                None,
            )
            if not target:
                raise HTTPException(status_code=404, detail="Member not found")
            self._assert_member_mutation_allowed(
                agency_id,
                target,
                next_role=None,
                next_status=None,
                actor_user_id=actor_user_id,
                actor_is_platform_admin=actor_is_platform_admin,
                require_actor=True,
            )
            self.members.pop(f"{agency_id}:{target.user_id}", None)
            self._rebuild_user_agency_access(target.user_id)

    def revoke_client(self, agency_id: UUID, access_id: UUID) -> None:
        self._agency_or_404(agency_id)
        target = next((c for c in self.clients.values() if c.id == access_id and c.agency_id == agency_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Client access binding not found")
        self.clients.pop(f"{agency_id}:{target.client_id}", None)
        for member in self.members.values():
            if member.agency_id == agency_id:
                self._rebuild_user_agency_access(member.user_id)

    def accept_invite(
        self,
        payload: AgencyInviteAcceptRequest,
        *,
        session_ttl_minutes: int,
        accepting_user_id: Optional[UUID] = None,
    ) -> AgencyInviteAcceptResponse:
        now = _utcnow()
        invite_id = self.invite_token_hash_to_id.get(_token_hash(payload.token.strip()))
        if not invite_id or invite_id not in self.invites:
            raise HTTPException(status_code=404, detail={"code": "invite_not_found", "message": "Invite token is invalid"})
        invite = self.invites[invite_id]
        if invite.status != "pending":
            raise HTTPException(status_code=409, detail={"code": "invite_not_pending", "message": "Invite is no longer pending"})
        if invite.expires_at < now:
            self.invites[invite_id] = invite.model_copy(update={"status": "expired", "updated_at": now})
            raise HTTPException(status_code=400, detail={"code": "invite_expired", "message": "Invite is expired"})

        agency = self._agency_or_404(invite.agency_id)
        if agency.status != "active":
            raise HTTPException(status_code=409, detail={"code": "agency_inactive", "message": "Agency is not active"})

        user = self.auth_store.find_user_by_email(invite.email)
        if user:
            if user.role != "agency":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "user_role_conflict", "message": "Existing user role cannot accept agency invite"},
                )
            if user.status != "active":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "existing_user_inactive", "message": "Existing user must be active to accept invite"},
                )
            if accepting_user_id != user.id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "existing_user_auth_required",
                        "message": "Sign in as the invited user before accepting this invite",
                    },
                )
        else:
            if accepting_user_id is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "invite_identity_mismatch",
                        "message": "Sign out before accepting an invite for a new user",
                    },
                )
            user = self.auth_store.create_user(
                UserCreate(
                    email=invite.email,
                    name=(payload.name.strip() if payload.name else SqlitePlatformAdminStore._derive_name_from_email(invite.email)),
                    role="agency",
                    status="active",
                )
            )
            password = (payload.password or "").strip()
            if len(password) < 8:
                password = f"{secrets.token_urlsafe(18)}Aa1"
            self.auth_store.set_password(user.id, password)

        member = self.upsert_member(
            invite.agency_id,
            AgencyMemberCreate(user_id=user.id, role=invite.member_role, status="active"),
        )
        accepted_invite = invite.model_copy(
            update={
                "status": "accepted",
                "accepted_user_id": user.id,
                "accepted_at": now,
                "updated_at": now,
            }
        )
        self.invites[invite_id] = accepted_invite
        session = self.auth_store.issue_session(SessionIssueRequest(user_id=user.id, ttl_minutes=session_ttl_minutes))
        return AgencyInviteAcceptResponse(
            invite=accepted_invite,
            agency=agency,
            member=member,
            user=user,
            session=session,
        )

