"""메시지 전송: 분류 → 상태 병합 → 서로 알아가기 갱신 → 이번 턴 질문 선택 → 응답 → 한 트랜잭션 저장."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from counseling import cards as card_deck
from counseling import rapport
from counseling.state import MergeError, merge, plan_turn
from llm.client import classify, generate_reply
from routes.common import (
    acquire_session_lock, history_and_turn, insert_assistant, llm_errors, load_messages, load_state,
    message_dict, require_session, save_state,
)
from storage.db import get_db

router = APIRouter()


class MessageRequest(BaseModel):
    content: str
    request_id: str


@router.post("/api/sessions/{session_id}/messages")
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

        history, turn = history_and_turn(load_messages(conn, session_id))

        with llm_errors(extra=(MergeError,)):
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

        # 4) 메시지 두 개와 state를 한 트랜잭션으로 저장
        conn.execute(
            "INSERT INTO messages (session_id, turn, role, content, request_id) VALUES (?, ?, 'user', ?, ?)",
            (session_id, turn, content, request.request_id),
        )
        message = insert_assistant(conn, session_id, turn, reply_text, suggestions)
        save_state(conn, session_id, state, next_id)
        conn.commit()
        return {"message": message, "state": state}
    finally:
        conn.close()
        lock.release()
