"""라포 카드: 한 장 선택, 무작위로 다시 보기, 건너뛰기."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from counseling import cards as card_deck
from llm.client import generate_reply
from routes.common import (
    acquire_session_lock, history_and_turn, insert_assistant, llm_errors, load_messages, load_state,
    require_pending_cards, require_session, save_state,
)
from storage.db import get_db

router = APIRouter()


class CardChoiceRequest(BaseModel):
    card_id: int


@router.post("/api/sessions/{session_id}/cards/choose")
def choose_card(session_id: str, request: CardChoiceRequest):
    """고른 카드는 확인된 기준이 되고, 사용자 메시지로 기록한 뒤 윤이가 그 기준으로 탐색을 시작한다."""
    lock = acquire_session_lock(session_id)
    conn = get_db()
    try:
        require_session(conn, session_id)
        state, next_id = load_state(conn, session_id)
        cards = require_pending_cards(state)
        chosen = next((c for c in cards["offered"] if c["id"] == request.card_id), None)
        if chosen is None:
            raise HTTPException(status_code=422, detail="제시된 카드 중에서 골라 주세요.")

        history, turn = history_and_turn(load_messages(conn, session_id))

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
        with llm_errors():
            reply_text, suggestions, _, _ = generate_reply(state, history, user_text, turn, plan)

        cur_user = conn.execute(
            "INSERT INTO messages (session_id, turn, role, content) VALUES (?, ?, 'user', ?)",
            (session_id, turn, user_text),
        )
        message = insert_assistant(conn, session_id, turn, reply_text, suggestions)
        save_state(conn, session_id, state, next_id)
        conn.commit()
        return {
            "messages": [{"id": cur_user.lastrowid, "turn": turn, "role": "user", "content": user_text}, message],
            "state": state,
        }
    finally:
        conn.close()
        lock.release()


@router.post("/api/sessions/{session_id}/cards/reshuffle")
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


@router.post("/api/sessions/{session_id}/cards/dismiss")
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
