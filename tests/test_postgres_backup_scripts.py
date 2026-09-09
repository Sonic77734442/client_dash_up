import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="Bash is required for PostgreSQL script checks")

FAKE_TOOL = """#!/usr/bin/env bash
set -eu
case "${0##*/}" in
  psql)
    echo "${TEST_SERVER_NUM:-170001}"
    ;;
  pg_dump)
    if [[ "${1:-}" == --version ]]; then
      echo "pg_dump (PostgreSQL) ${TEST_DUMP_MAJOR:-17}.1"
      exit 0
    fi
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == --file ]]; then shift; OUT="$1"; fi
      shift
    done
    printf 'fake archive' > "$OUT"
    exit "${TEST_DUMP_FAIL:-0}"
    ;;
  pg_restore)
    if [[ "${1:-}" == --list ]]; then
      [[ "${TEST_LIST_FAIL:-0}" == 0 ]] || exit 2
      echo "; Dumped by pg_dump version: ${TEST_DUMP_MAJOR:-17}.1"
      exit 0
    fi
    printf '%s\\n' "$*" >> "$TEST_LOG"
    exit "${TEST_RESTORE_FAIL:-0}"
    ;;
esac
"""


@pytest.fixture
def script_env(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("pg_dump", "pg_restore", "psql"):
        script = tools / name
        script.write_text(FAKE_TOOL, encoding="utf-8", newline="\n")
        script.chmod(0o755)
    backup_dir = tmp_path / "backups"
    log = tmp_path / "restore.log"
    env = dict(os.environ, DATABASE_URL="postgresql://example.invalid/disposable",
               BACKUP_DIR=backup_dir.as_posix(), TEST_LOG=log.as_posix(),
               PATH=str(tools) + os.pathsep + os.environ.get("PATH", ""))
    return env, backup_dir, log


def run(script, env, *args):
    return subprocess.run([BASH, (ROOT / "scripts" / script).as_posix(), *args],
                          env=env, cwd=ROOT, text=True, capture_output=True, timeout=60 if os.name == "nt" else 15)


def test_backup_publishes_only_a_validated_archive(script_env):
    env, backups, _log = script_env
    result = run("backup_postgres.sh", env)
    assert result.returncode == 0, result.stderr
    assert len(list(backups.glob("*.dump"))) == 1
    assert list(backups.glob("*.partial")) == []


@pytest.mark.parametrize("failure", ["TEST_DUMP_FAIL", "TEST_LIST_FAIL"])
def test_failed_backup_does_not_leave_a_completed_or_partial_archive(script_env, failure):
    env, backups, _log = script_env
    env[failure] = "2"
    result = run("backup_postgres.sh", env)
    assert result.returncode != 0
    assert list(backups.glob("*.dump")) == []
    assert list(backups.glob("*.partial")) == []


def test_backup_rejects_older_client_before_creating_an_archive(script_env):
    env, backups, _log = script_env
    env["TEST_SERVER_NUM"] = "180001"
    result = run("backup_postgres.sh", env)
    assert result.returncode != 0
    assert "cannot back up PostgreSQL 18" in result.stderr
    assert not backups.exists()


def test_restore_uses_one_transaction_and_reports_errors(script_env, tmp_path):
    env, _backups, log = script_env
    archive = tmp_path / "valid.dump"
    archive.write_bytes(b"fake archive")
    env["TEST_RESTORE_FAIL"] = "7"
    result = run("restore_postgres.sh", env, archive.as_posix(), "--confirm-replace-target")
    assert result.returncode == 7
    assert "--single-transaction" in log.read_text()
    assert "--exit-on-error" in log.read_text()
    assert "restore completed" not in result.stdout


@pytest.mark.parametrize("failure", ["invalid_archive", "older_target"])
def test_restore_checks_archive_and_versions_before_modifying_target(script_env, tmp_path, failure):
    env, _backups, log = script_env
    archive = tmp_path / "input.dump"
    archive.write_bytes(b"fake archive")
    if failure == "invalid_archive":
        env["TEST_LIST_FAIL"] = "2"
    else:
        env["TEST_SERVER_NUM"] = "160001"
    result = run("restore_postgres.sh", env, archive.as_posix(), "--confirm-replace-target")
    assert result.returncode != 0
    assert not log.exists()
