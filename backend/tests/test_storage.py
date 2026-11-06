import sqlite3

import pytest

from routes import sessions
from storage import db


# 초기화를 반복해도 이미 저장한 상담과 자동 증가 메시지 ID를 보존한다.
def test_initialization_preserves_sessions_and_message_ids(database):
    first = sessions.create_session(sessions.SessionRequest(preferred_name="첫 상담"))
    db.init_db()
    second = sessions.create_session(sessions.SessionRequest(preferred_name="다음 상담"))
    assert second["message"]["id"] > first["message"]["id"]
    assert sessions.get_session(first["session_id"])["messages"][0] == first["message"]


# 따옴표·물음표가 들어간 값도 SQL과 분리되어 저장되고, commit 전 종료는 취소된다.
def test_parameters_and_rollback(database):
    conn = db.get_db()
    try:
        conn.execute("INSERT INTO sessions (id) VALUES ($1)", ("따옴표'와 물음표?",))
        assert conn.execute("SELECT id FROM sessions WHERE id = $1", ("따옴표'와 물음표?",)).fetchone()
    finally:
        conn.close()
    conn = db.get_db()
    try:
        assert conn.execute("SELECT id FROM sessions WHERE id = $1", ("따옴표'와 물음표?",)).fetchone() is None
    finally:
        conn.close()


# Render에서 DB 주소를 빠뜨렸을 때 임시 파일에 상담을 저장하지 않고 시작을 중단한다.
def test_render_requires_database_url(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db.get_db()


# 명령문 전체나 예시 문구를 붙인 잘못된 주소는 자격 증명 출력 없이 안내한다.
def test_invalid_database_url_is_rejected(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "psql 'not-a-database-url'")
    with pytest.raises(ValueError, match="postgresql://"):
        db.get_db()


# 예전 SQLite의 suggestions 열을 보완하면서 기존 메시지를 유지한다.
def test_legacy_sqlite_migration_preserves_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO messages VALUES (1, '기존 상담')")
    db.init_db()
    with db.get_db() as conn:
        row = conn.execute("SELECT content, suggestions FROM messages WHERE id = 1").fetchone()
        assert row["content"] == "기존 상담"
        assert row["suggestions"] is None
