import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "chatbot.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            turn INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            request_id TEXT UNIQUE,
            suggestions TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 이전 버전 DB에 suggestions 컬럼이 없으면 추가한다
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(messages)")]
    if "suggestions" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN suggestions TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_state (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id),
            state_json TEXT NOT NULL,
            next_item_id INTEGER NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS career_maps (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id),
            map_json TEXT NOT NULL,
            based_on_message_id INTEGER NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
