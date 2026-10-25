import json
import threading
import uuid

import openai
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from career_map import CareerMapError, build_career_map
from db import get_db, init_db
from llm import LLMConfigError, LLMResponseError, classify, generate_reply, summarize
from prompts import PERSONAS
from state import TONES, MergeError, empty_state, merge, plan_turn
import cards as card_deck
import rapport

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
        state = json.loads(row["state_json"])
        state.setdefault("rapport", rapport.empty_rapport())  # 이전 버전 상담에는 rapport가 없다
        return state, row["next_item_id"]
    return empty_state(), 1


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


def acquire_session_lock(session_id):
    lock = session_lock(session_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="이전 요청을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.")
    return lock


class MessageRequest(BaseModel):
    content: str
    request_id: str


class SessionRequest(BaseModel):
    preferred_name: str
    tone: str = "counselor"


@app.post("/api/sessions")
def create_session(request: SessionRequest):
    """시작 화면에서 받은 이름·말투로 상담을 만들고, 윤이의 인사를 첫 메시지(turn 0)로 넣는다."""
    name = request.preferred_name.strip()
    if not name or len(name) > 20:
        raise HTTPException(status_code=422, detail="이름이나 호칭을 1~20자로 입력해 주세요.")
    if request.tone not in TONES:
        raise HTTPException(status_code=422, detail="말투는 friend, counselor, spicy 중 하나여야 합니다.")
    session_id = str(uuid.uuid4())
    state = empty_state()
    state["preferred_name"] = name
    state["tone"] = request.tone
    greeting = PERSONAS[request.tone]["greeting"]
    content = greeting["content"].format(name=name)
    conn = get_db()
    conn.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
    conn.execute(
        "INSERT INTO session_state (session_id, state_json, next_item_id) VALUES (?, ?, 1)",
        (session_id, json.dumps(state, ensure_ascii=False)),
    )
    cur = conn.execute(
        "INSERT INTO messages (session_id, turn, role, content, suggestions) VALUES (?, 0, 'assistant', ?, ?)",
        (session_id, content, json.dumps(greeting["suggestions"], ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return {
        "session_id": session_id,
        "state": state,
        "message": {
            "id": cur.lastrowid,
            "turn": 0,
            "role": "assistant",
            "content": content,
            "suggestions": greeting["suggestions"],
        },
    }


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
                "SELECT id, turn, role, content, suggestions FROM messages "
                "WHERE session_id = ? AND turn = ? AND role = 'assistant'",
                (session_id, dup["turn"]),
            ).fetchone()
            return {"message": message_dict(reply), "state": state}

        rows = load_messages(conn, session_id)
        history = [{"role": r["role"], "content": r["content"]} for r in rows]
        turn = sum(1 for r in rows if r["role"] == "user") + 1

        try:
            # 1) 분류 → 2) 코드 병합 → 3) 갱신된 state로 응답 생성
            result = classify(state, history, content)
            state, next_id = merge(state, result, turn, next_id)
            # 서로 알아가기 신호 반영 → 이번 턴의 질문 하나 선택(확인 → 후속 → 라포) → 응답 → 실제로 물은 질문만 asked로
            cur_before = rapport.current_question(state["rapport"])
            state["rapport"] = rapport.apply_classification(state["rapport"], result, turn)
            plan = plan_turn(state, result, turn, cur_before["id"] if cur_before else None)
            reply_text, suggestions, asked_id, picks = generate_reply(state, history, content, turn, plan)
            rapport.apply_reply(state["rapport"], plan["rapport_action"], asked_id)
            # 라포가 끝나는 턴에 카드 3장을 제시한다. 대화 기반(근거 있는 카드) 또는 무작위
            action_type = plan["rapport_action"]["type"]
            if action_type == "wrap_up":
                state["rapport"]["cards"] = card_deck.new_cards(card_deck.offer_from_picks(picks), "conversation", turn)
            elif action_type == "concern":
                state["rapport"]["cards"] = card_deck.new_cards(card_deck.random_offer(), "random", turn)
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
            "INSERT INTO messages (session_id, turn, role, content, suggestions) VALUES (?, ?, 'assistant', ?, ?)",
            (session_id, turn, reply_text, json.dumps(suggestions, ensure_ascii=False)),
        )
        save_state(conn, session_id, state, next_id)
        conn.commit()
        return {
            "message": {
                "id": cur.lastrowid,
                "turn": turn,
                "role": "assistant",
                "content": reply_text,
                "suggestions": suggestions,
            },
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
        if not any(r["role"] == "user" for r in rows):
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


class CardChoiceRequest(BaseModel):
    card_id: int


def save_state(conn, session_id, state, next_id):
    conn.execute(
        "INSERT INTO session_state (session_id, state_json, next_item_id) VALUES (?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET state_json = excluded.state_json, "
        "next_item_id = excluded.next_item_id, updated_at = CURRENT_TIMESTAMP",
        (session_id, json.dumps(state, ensure_ascii=False), next_id),
    )


def require_pending_cards(state):
    cards = state["rapport"].get("cards")
    if not card_deck.pending(cards):
        raise HTTPException(status_code=422, detail="지금 고를 수 있는 카드가 없습니다.")
    return cards


@app.post("/api/sessions/{session_id}/cards/choose")
def choose_card(session_id: str, request: CardChoiceRequest):
    """라포 카드 한 장 선택. 고른 카드는 확인된 기준이 되고, 사용자 메시지로 기록한 뒤 윤이가 그 기준으로 탐색을 시작한다."""
    lock = acquire_session_lock(session_id)
    conn = get_db()
    try:
        require_session(conn, session_id)
        state, next_id = load_state(conn, session_id)
        cards = require_pending_cards(state)
        chosen = next((c for c in cards["offered"] if c["id"] == request.card_id), None)
        if chosen is None:
            raise HTTPException(status_code=422, detail="제시된 카드 중에서 골라 주세요.")

        rows = load_messages(conn, session_id)
        history = [{"role": r["role"], "content": r["content"]} for r in rows]
        turn = sum(1 for r in rows if r["role"] == "user") + 1

        cards["chosen_id"] = chosen["id"]
        state["criteria"].append({
            "id": next_id,
            "text": chosen["criterion"],
            "evidence": chosen.get("evidence") or f"라포 카드 '{chosen['name']}' 선택",
            "turn": turn,
            "status": "confirmed",
        })
        next_id += 1
        user_text = f"'{chosen['name']}' 카드를 골랐어요. ({chosen['meaning']})"
        plan = {
            "question": "card_focus", "confirm_items": [], "new_texts": [],
            "rapport_action": {"type": "none", "question": None}, "card": chosen,
        }
        try:
            reply_text, suggestions, _, _ = generate_reply(state, history, user_text, turn, plan)
        except LLMConfigError as e:
            raise HTTPException(status_code=500, detail=str(e))
        except openai.AuthenticationError:
            raise HTTPException(status_code=500, detail="API 키가 유효하지 않습니다. .env의 OPENAI_API_KEY를 확인하세요.")
        except (openai.APIError, LLMResponseError):
            raise HTTPException(status_code=502, detail="모델 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.")

        cur_user = conn.execute(
            "INSERT INTO messages (session_id, turn, role, content) VALUES (?, ?, 'user', ?)",
            (session_id, turn, user_text),
        )
        cur = conn.execute(
            "INSERT INTO messages (session_id, turn, role, content, suggestions) VALUES (?, ?, 'assistant', ?, ?)",
            (session_id, turn, reply_text, json.dumps(suggestions, ensure_ascii=False)),
        )
        save_state(conn, session_id, state, next_id)
        conn.commit()
        return {
            "messages": [
                {"id": cur_user.lastrowid, "turn": turn, "role": "user", "content": user_text},
                {"id": cur.lastrowid, "turn": turn, "role": "assistant", "content": reply_text, "suggestions": suggestions},
            ],
            "state": state,
        }
    finally:
        conn.close()
        lock.release()


@app.post("/api/sessions/{session_id}/cards/reshuffle")
def reshuffle_cards(session_id: str):
    """지금 제시된 3장 대신 무작위 3장을 다시 보여준다. 모델 호출 없음."""
    lock = acquire_session_lock(session_id)
    conn = get_db()
    try:
        require_session(conn, session_id)
        state, next_id = load_state(conn, session_id)
        cards = require_pending_cards(state)
        exclude = [c["id"] for c in cards["offered"]]
        state["rapport"]["cards"] = card_deck.new_cards(card_deck.random_offer(exclude), "random", cards["turn"])
        save_state(conn, session_id, state, next_id)
        conn.commit()
        return {"state": state}
    finally:
        conn.close()
        lock.release()


@app.post("/api/sessions/{session_id}/cards/dismiss")
def dismiss_cards(session_id: str):
    """카드를 고르지 않고 넘어간다. 대화는 그대로 이어진다."""
    lock = acquire_session_lock(session_id)
    conn = get_db()
    try:
        require_session(conn, session_id)
        state, next_id = load_state(conn, session_id)
        cards = require_pending_cards(state)
        cards["dismissed"] = True
        save_state(conn, session_id, state, next_id)
        conn.commit()
        return {"state": state}
    finally:
        conn.close()
        lock.release()
