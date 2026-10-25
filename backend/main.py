import json
import threading
import uuid

import openai
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from career_map import CareerMapError, build_career_map
from db import get_db, init_db
from llm import LLMConfigError, LLMResponseError, classify, generate_reply, summarize
from state import MergeError, empty_state, merge

app = FastAPI()
init_db()

# 상담별 lock. 같은 상담에 전송·정리 요청이 겹치면 409를 반환한다.
_locks = {}
_locks_guard = threading.Lock()


def session_lock(session_id):
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


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
        return json.loads(row["state_json"]), row["next_item_id"]
    return empty_state(), 1


def load_career_map(conn, session_id):
    row = conn.execute("SELECT map_json FROM career_maps WHERE session_id = ?", (session_id,)).fetchone()
    return json.loads(row["map_json"]) if row else None


def load_messages(conn, session_id):
    rows = conn.execute(
        "SELECT id, turn, role, content FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def acquire_session_lock(session_id):
    lock = session_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="이전 요청을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.")
    return lock


class MessageRequest(BaseModel):
    content: str
    request_id: str


@app.post("/api/sessions")
def create_session():
    session_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
    conn.commit()
    conn.close()
    return {"session_id": session_id}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    conn = get_db()
    try:
        require_session(conn, session_id)
        messages = load_messages(conn, session_id)
        state, _ = load_state(conn, session_id)
        return {
            "messages": messages,
            "state": state,
            "career_map": load_career_map(conn, session_id),
            "last_message_id": messages[-1]["id"] if messages else 0,
        }
    finally:
        conn.close()


@app.post("/api/sessions/{session_id}/messages")
def send_message(session_id: str, request: MessageRequest):
    content = request.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="메시지가 비어 있습니다.")

    lock = acquire_session_lock(session_id)
    conn = get_db()
    try:
        require_session(conn, session_id)
        state, next_id = load_state(conn, session_id)

        # request_id 중복 확인 (lock 안에서 수행)
        dup = conn.execute(
            "SELECT session_id, turn, content FROM messages WHERE request_id = ?",
            (request.request_id,),
        ).fetchone()
        if dup:
            if dup["session_id"] != session_id or dup["content"] != content:
                raise HTTPException(status_code=409, detail="같은 요청 ID로 다른 내용이 전송되었습니다.")
            reply = conn.execute(
                "SELECT id, turn, role, content FROM messages "
                "WHERE session_id = ? AND turn = ? AND role = 'assistant'",
                (session_id, dup["turn"]),
            ).fetchone()
            return {"message": dict(reply), "state": state}

        rows = load_messages(conn, session_id)
        history = [{"role": r["role"], "content": r["content"]} for r in rows]
        turn = sum(1 for r in rows if r["role"] == "user") + 1

        try:
            # 1) 분류 → 2) 코드 병합 → 3) 갱신된 state로 응답 생성
            result = classify(state, history, content)
            state, next_id = merge(state, result, turn, next_id)
            reply_text = generate_reply(state, history, content, turn)
        except LLMConfigError as e:
            raise HTTPException(status_code=500, detail=str(e))
        except openai.AuthenticationError:
            raise HTTPException(status_code=500, detail="API 키가 유효하지 않습니다. .env의 OPENAI_API_KEY를 확인하세요.")
        except (openai.APIError, LLMResponseError, MergeError):
            raise HTTPException(status_code=502, detail="모델 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.")

        # 4) 메시지 두 개와 state를 한 트랜잭션으로 저장
        conn.execute(
            "INSERT INTO messages (session_id, turn, role, content, request_id) VALUES (?, ?, 'user', ?, ?)",
            (session_id, turn, content, request.request_id),
        )
        cur = conn.execute(
            "INSERT INTO messages (session_id, turn, role, content) VALUES (?, ?, 'assistant', ?)",
            (session_id, turn, reply_text),
        )
        conn.execute(
            "INSERT INTO session_state (session_id, state_json, next_item_id) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET state_json = excluded.state_json, "
            "next_item_id = excluded.next_item_id, updated_at = CURRENT_TIMESTAMP",
            (session_id, json.dumps(state, ensure_ascii=False), next_id),
        )
        conn.commit()
        return {
            "message": {"id": cur.lastrowid, "turn": turn, "role": "assistant", "content": reply_text},
            "state": state,
        }
    finally:
        conn.close()
        lock.release()


@app.post("/api/sessions/{session_id}/summary")
def summarize_session(session_id: str):
    """'현재까지 정리하기'. 선택 기준·후보 비교·다음 탐색 행동을 정리해 career_maps에 덮어쓴다."""
    lock = acquire_session_lock(session_id)
    conn = get_db()
    try:
        require_session(conn, session_id)
        rows = load_messages(conn, session_id)
        if not rows:
            raise HTTPException(status_code=422, detail="정리할 대화가 아직 없습니다. 먼저 대화를 나눠 주세요.")
        state, _ = load_state(conn, session_id)
        history = [{"role": r["role"], "content": r["content"]} for r in rows]

        try:
            output = summarize(state, history)
            career_map = build_career_map(state, output, rows[-1]["id"])
        except LLMConfigError as e:
            raise HTTPException(status_code=500, detail=str(e))
        except openai.AuthenticationError:
            raise HTTPException(status_code=500, detail="API 키가 유효하지 않습니다. .env의 OPENAI_API_KEY를 확인하세요.")
        except (openai.APIError, LLMResponseError, CareerMapError):
            raise HTTPException(status_code=502, detail="정리 결과를 받지 못했습니다. 잠시 후 다시 시도해 주세요.")

        conn.execute(
            "INSERT INTO career_maps (session_id, map_json, based_on_message_id) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET map_json = excluded.map_json, "
            "based_on_message_id = excluded.based_on_message_id, updated_at = CURRENT_TIMESTAMP",
            (session_id, json.dumps(career_map, ensure_ascii=False), career_map["based_on_message_id"]),
        )
        conn.commit()
        return {"career_map": career_map}
    finally:
        conn.close()
        lock.release()
