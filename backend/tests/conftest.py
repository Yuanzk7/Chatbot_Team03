"""기본 검증은 임시 SQLite, 명시적으로 요청한 PostgreSQL 검증은 독립 스키마를 사용한다."""
import os
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from storage import db  # noqa: E402


# 로컬 .env의 Neon 주소가 일반 테스트에 사용되지 않도록 매 테스트마다 환경을 분리한다.
@pytest.fixture(autouse=True)
def local_database_environment(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)


# 기본적으로 임시 파일을 쓴다. CAREERMATE_POSTGRES_TESTS=1일 때만 Neon에 연결한다.
# PostgreSQL 검증은 고유 스키마에 한정하고 종료 후 그 스키마만 삭제한다.
@pytest.fixture
def database(tmp_path, monkeypatch, local_database_environment):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "chatbot.db")
    if os.getenv("CAREERMATE_POSTGRES_TESTS") != "1":
        db.init_db()
        yield
        return

    from psycopg import sql
    from routes import cards, messages, sessions, summary

    database_url = os.getenv("CAREERMATE_TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not database_url:
        pytest.fail("PostgreSQL 검증에 사용할 연결 주소를 설정해 주세요.")
    monkeypatch.setenv("DATABASE_URL", database_url)
    schema = sql.Identifier("careermate_test_" + uuid.uuid4().hex)
    connect = db.get_db
    admin = connect()
    admin.autocommit = True
    admin.execute(sql.SQL("CREATE SCHEMA {}").format(schema))

    # 모든 라우트와 테이블 초기화가 검증용 스키마에서만 읽고 쓰도록 연결을 바꾼다.
    def connect_test_schema():
        conn = connect()
        # Neon의 트랜잭션 풀링에서도 같은 트랜잭션 전체를 검증용 스키마에 고정한다.
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(schema))
        return conn

    for module in (db, cards, messages, sessions, summary):
        monkeypatch.setattr(module, "get_db", connect_test_schema)
    try:
        db.init_db()
        yield
    finally:
        try:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))
        finally:
            admin.close()
