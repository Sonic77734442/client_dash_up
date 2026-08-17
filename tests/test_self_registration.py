from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app import main as main_module


client = TestClient(main_module.app)


def _registration_db(tmp_path, monkeypatch):
    db_path = tmp_path / "registration.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE phone_verifications (
          phone TEXT PRIMARY KEY,
          code_hash TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          verified_at TEXT NULL,
          verification_token_hash TEXT NULL,
          consumed_at TEXT NULL,
          last_sent_at TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE registration_profiles (
          user_id TEXT PRIMARY KEY,
          company TEXT NULL,
          phone TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL
        );
        """
    )
    connection.close()

    @contextmanager
    def fake_sqlite_conn(_db_path):
        conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    sent = {}

    def fake_send(phone: str, code: str) -> None:
        sent["phone"] = phone
        sent["code"] = code

    monkeypatch.setattr(main_module, "sqlite_conn", fake_sqlite_conn)
    monkeypatch.setattr(main_module, "_send_whatsapp_verification_code", fake_send)
    monkeypatch.setenv("WHATSAPP_VERIFICATION_SECRET", "test-self-registration-secret-123")
    return sent


def test_phone_verification_and_self_registration(tmp_path, monkeypatch):
    client.cookies.clear()
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    sent = _registration_db(tmp_path, monkeypatch)

    send = client.post(
        "/auth/phone-verification/send",
        json={"phone": "+7 700 123 45 67"},
    )
    assert send.status_code == 200, send.text
    assert sent["phone"] == "+77001234567"
    assert len(sent["code"]) == 6

    confirm = client.post(
        "/auth/phone-verification/confirm",
        json={"phone": "+7 700 123 45 67", "code": sent["code"]},
    )
    assert confirm.status_code == 200, confirm.text

    registration = client.post(
        "/auth/register",
        json={
            "name": "Anna Marketer",
            "company": "ACME Corp",
            "phone": "+7 700 123 45 67",
            "email": "anna@example.test",
            "password": "correct-horse-123",
            "phone_verification_token": confirm.json()["verification_token"],
        },
    )
    assert registration.status_code == 200, registration.text
    body = registration.json()
    assert body["user"]["role"] == "solo_client"
    assert body["user"]["email"] == "anna@example.test"
    assert len(body["session"]["accessible_client_ids"]) == 1
    assert main_module.settings.auth_cookie_name in client.cookies

    me = client.get("/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["id"] == body["user"]["id"]

    reused = client.post(
        "/auth/register",
        json={
            "name": "Another User",
            "company": None,
            "phone": "+7 700 123 45 67",
            "email": "another@example.test",
            "password": "correct-horse-456",
            "phone_verification_token": confirm.json()["verification_token"],
        },
    )
    assert reused.status_code in {400, 409}


def test_phone_verification_rejects_incorrect_code(tmp_path, monkeypatch):
    client.cookies.clear()
    assert client.post("/_testing/use-inmemory-stores").status_code == 200
    sent = _registration_db(tmp_path, monkeypatch)
    assert client.post(
        "/auth/phone-verification/send",
        json={"phone": "+7 701 765 43 21"},
    ).status_code == 200

    incorrect = "000000" if sent["code"] != "000000" else "111111"
    response = client.post(
        "/auth/phone-verification/confirm",
        json={"phone": sent["phone"], "code": incorrect},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "phone_code_incorrect"

