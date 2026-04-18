#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def seed(reset: bool = True):
    from app.settings import get_settings

    settings = get_settings()
    db_path = Path(settings.budgets_db_path)
    if reset and db_path.exists():
        db_path.unlink()

    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    def post(path: str, payload: dict, headers: dict | None = None):
        r = client.post(path, json=payload, headers=headers or {})
        if r.status_code >= 400:
            raise RuntimeError(f"{path} failed: {r.status_code} {r.text}")
        return r.json()

    # internal users
    admin = post("/auth/internal/users", {"email": "admin@demo.local", "name": "Demo Admin", "role": "admin", "status": "active"})
    agency_user = post("/auth/internal/users", {"email": "agency@demo.local", "name": "Demo Agency", "role": "agency", "status": "active"})
    client_user = post("/auth/internal/users", {"email": "client@demo.local", "name": "Demo Client User", "role": "client", "status": "active"})
    admin_session = post("/auth/internal/sessions/issue", {"user_id": admin["id"], "ttl_minutes": 1440})
    admin_headers = {"Authorization": f"Bearer {admin_session['token']}"}

    # provider identity links (architecture-only)
    post(
        "/auth/internal/identities/link",
        {
            "user_id": agency_user["id"],
            "provider": "google",
            "provider_user_id": "demo-google-agency-1",
            "email": agency_user["email"],
            "email_verified": True,
            "raw_profile": {"name": agency_user["name"]},
        },
    )

    # demo tenants
    client_a = post("/clients", {"name": "Acme Travel", "status": "active", "default_currency": "USD", "timezone": "UTC"}, headers=admin_headers)
    client_b = post("/clients", {"name": "Nova Fitness", "status": "active", "default_currency": "USD", "timezone": "UTC"}, headers=admin_headers)

    # platform admin provisioning (agency -> members -> client bindings)
    demo_agency = post(
        "/platform/agencies",
        {"name": "Demo Growth Agency", "slug": "demo-growth", "status": "active", "plan": "starter"},
        headers=admin_headers,
    )
    post(
        f"/platform/agencies/{demo_agency['id']}/members",
        {"user_id": agency_user["id"], "role": "owner", "status": "active"},
        headers=admin_headers,
    )
    post(
        f"/platform/agencies/{demo_agency['id']}/clients",
        {"client_id": client_a["id"]},
        headers=admin_headers,
    )
    post(
        f"/platform/agencies/{demo_agency['id']}/clients",
        {"client_id": client_b["id"]},
        headers=admin_headers,
    )

    # direct tenant access (client user)
    post("/auth/internal/access", {"user_id": client_user["id"], "client_id": client_a["id"], "role": "client"})

    # ad accounts
    a_meta = post(
        "/ad-accounts",
        {
            "client_id": client_a["id"],
            "platform": "meta",
            "external_account_id": "demo-meta-acme",
            "name": "Acme Meta",
            "currency": "USD",
            "status": "active",
        },
        headers=admin_headers,
    )
    a_google = post(
        "/ad-accounts",
        {
            "client_id": client_a["id"],
            "platform": "google",
            "external_account_id": "demo-google-acme",
            "name": "Acme Google",
            "currency": "USD",
            "status": "active",
        },
        headers=admin_headers,
    )
    b_tiktok = post(
        "/ad-accounts",
        {
            "client_id": client_b["id"],
            "platform": "tiktok",
            "external_account_id": "demo-tiktok-nova",
            "name": "Nova TikTok",
            "currency": "USD",
            "status": "active",
        },
        headers=admin_headers,
    )

    # budgets
    budget_client_a = post(
        "/budgets",
        {
            "client_id": client_a["id"],
            "scope": "client",
            "amount": "2500.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "note": "Acme monthly budget",
        },
        headers=admin_headers,
    )
    budget_account_meta = post(
        "/budgets",
        {
            "client_id": client_a["id"],
            "scope": "account",
            "account_id": a_meta["id"],
            "amount": "1200.00",
            "currency": "USD",
            "period_type": "monthly",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "note": "Acme Meta budget",
        },
        headers=admin_headers,
    )

    # ad stats
    ingest_payload = {
        "rows": [
            {
                "ad_account_id": a_meta["id"],
                "date": "2026-04-01",
                "platform": "meta",
                "impressions": 10000,
                "clicks": 900,
                "spend": "420.00",
                "conversions": "32.00",
            },
            {
                "ad_account_id": a_google["id"],
                "date": "2026-04-01",
                "platform": "google",
                "impressions": 7000,
                "clicks": 500,
                "spend": "310.00",
                "conversions": "28.00",
            },
            {
                "ad_account_id": b_tiktok["id"],
                "date": "2026-04-01",
                "platform": "tiktok",
                "impressions": 15000,
                "clicks": 1100,
                "spend": "510.00",
                "conversions": "40.00",
            },
        ]
    }
    post("/ad-stats/ingest", ingest_payload, headers={"Idempotency-Key": "demo-seed-ingest-1", **admin_headers})

    # demo sessions
    agency_session = post("/auth/internal/sessions/issue", {"user_id": agency_user["id"], "ttl_minutes": 1440})

    # ready-check snapshots
    overview_a = client.get(
        f"/insights/overview?client_id={client_a['id']}&date_from=2026-04-01&date_to=2026-04-30&as_of_date=2026-04-15",
        headers=admin_headers,
    ).json()
    agency_overview = client.get("/agency/overview?date_from=2026-04-01&date_to=2026-04-30", headers=admin_headers).json()

    out = {
        "status": "ok",
        "db_path": str(db_path),
        "users": {"admin": admin, "agency": agency_user, "client": client_user},
        "agencies": [demo_agency],
        "clients": [client_a, client_b],
        "ad_accounts": [a_meta, a_google, b_tiktok],
        "budgets": [budget_client_a, budget_account_meta],
        "sessions": {
            "admin_token": admin_session["token"],
            "agency_token": agency_session["token"],
        },
        "samples": {
            "insights_overview_client_a": overview_a,
            "agency_overview": agency_overview,
        },
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Seed demo data for local frontend development")
    parser.add_argument("--no-reset", action="store_true", help="Do not remove existing local sqlite db before seeding")
    args = parser.parse_args()
    seed(reset=not args.no_reset)


if __name__ == "__main__":
    main()
