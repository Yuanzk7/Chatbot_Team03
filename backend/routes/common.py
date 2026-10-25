"""라우트들이 함께 쓰는 것: 상담별 lock, 상담·상태·메시지 읽기/쓰기, 모델 오류 변환."""
import json
import threading
from contextlib import contextmanager

import openai
from fastapi import HTTPException

from counseling import cards as card_deck
from counseling.rapport import empty_rapport
from counseling.state import empty_state
from llm.client import LLMConfigError, LLMResponseError

# 상담별 lock. 같은 상담에 전송·정리·카드 요청이 겹치면 409를 반환한다
_locks = {}
_locks_guard = threading.Lock()


def acquire_session_lock(session_id):
    with _locks_guard:
        lock = _locks.setdefault(session_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="이전 요청을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.")
    return lock


def require_session(conn, session_id):
    row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="상담을 찾을 수 없습니다.")


def load_state(conn, session_id):
    """(state, next_item_id). 아직 없으면 빈 상태."""
    row = conn.execute(
        "SELECT state_json, next_item_id FROM session_state WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row:
        state = json.loads(row["state_json"])
        state.setdefault("rapport", empty_rapport())  # 이전 버전 상담에는 rapport가 없다
        return state, row["next_item_id"]
    return empty_state(), 1


def save_state(conn, session_id, state, next_id):
    conn.execute(
        "INSERT INTO session_state (session_id, state_json, next_item_id) VALUES (?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET state_json = excluded.state_json, "
        "next_item_id = excluded.next_item_id, updated_at = CURRENT_TIMESTAMP",
        (session_id, json.dumps(state, ensure_ascii=False), next_id),
    )


def load_career_map(conn, session_id):
    row = conn.execute("SELECT map_json FROM career_maps WHERE session_id = ?", (session_id,)).fetchone()
    return json.loads(row["map_json"]) if row else None


def message_dict(row):
    m = {"id": row["id"], "turn": row["turn"], "role": row["role"], "content": row["content"]}
    if row["role"] == "assistant":
        m["suggestions"] = json.loads(row["suggestions"]) if row["suggestions"] else []
    return m


def load_messages(conn, session_id):
    rows = conn.execute(
        "SELECT id, turn, role, content, suggestions FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [message_dict(r) for r in rows]


def history_and_turn(rows):
    """모델에 보낼 대화 기록과 이번 턴 번호(사용자 메시지 수 + 1)."""
    history = [{"role": r["role"], "content": r["content"]} for r in rows]
    turn = sum(1 for r in rows if r["role"] == "user") + 1
    return history, turn


def insert_assistant(conn, session_id, turn, content, suggestions):
    cur = conn.execute(
        "INSERT INTO messages (session_id, turn, role, content, suggestions) VALUES (?, ?, 'assistant', ?, ?)",
        (session_id, turn, content, json.dumps(suggestions, ensure_ascii=False)),
    )
    return {"id": cur.lastrowid, "turn": turn, "role": "assistant", "content": content, "suggestions": suggestions}


def require_pending_cards(state):
    cards = state["rapport"].get("cards")
    if not card_deck.pending(cards):
        raise HTTPException(status_code=422, detail="지금 고를 수 있는 카드가 없습니다.")
    return cards


@contextmanager
def llm_errors(detail_502="모델 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.", extra=()):
    """모델 호출 중 생기는 예외를 사용자 안내가 담긴 HTTP 오류로 바꾼다."""
    try:
        yield
    except LLMConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except openai.AuthenticationError:
        raise HTTPException(status_code=500, detail="API 키가 유효하지 않습니다. .env의 OPENAI_API_KEY를 확인하세요.")
    except (openai.APIError, LLMResponseError, *extra):
        raise HTTPException(status_code=502, detail=detail_502)
