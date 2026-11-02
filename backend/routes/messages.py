"""메시지 전송: 분류 → 상태 병합 → 서로 알아가기 갱신 → 이번 턴 질문 선택 → 응답 → 한 트랜잭션 저장."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from counseling import cards as card_deck
from counseling import rapport
from counseling.controller import complete_turn, decision_record, plan_turn
from counseling.state import MergeError, merge
from llm.client import classify, generate_reply
from routes.common import (
    acquire_session_lock, history_and_turn, insert_assistant, llm_errors, load_messages, load_state,
    message_dict, require_session, save_controller_event, save_state,
)
from storage.db import get_db

router = APIRouter()


class MessageRequest(BaseModel):
    content: str
    request_id: str


# 사용자 입력을 분류하고 통제 계획을 적용한 뒤, 응답·상태·판정 기록을 함께 저장한다.
# 같은 request_id의 재전송은 저장된 응답을 반환하므로 탐색 턴 수와 로그가 중복 증가하지 않는다.
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

        history, turn = history_and_turn(load_messages(conn, session_id), state)

        with llm_errors(extra=(MergeError,)):
            # 1) 분류 → 2) 상태 병합 → 3) 규칙 판정 → 4) 검증된 응답 생성
            result = classify(state, history, content)
            state, next_id = merge(state, result, turn, next_id)
            # 라포 진행과 세 통제 규칙이 서로 다른 질문을 내지 않도록 컨트롤러가 한 계획으로 조정한다.
            cur_before = rapport.current_question(state["rapport"])
            plan = plan_turn(state, result, turn, cur_before["id"] if cur_before else None)
            reply = generate_reply(state, history, content, turn, plan)
            rapport.apply_reply(state["rapport"], plan["rapport_action"], reply.asked_question_id)
            # 라포가 끝나는 턴에 카드 3장을 제시한다. 대화 기반(근거 있는 카드) 또는 무작위
            action_type = plan["rapport_action"]["type"]
            if action_type == "wrap_up":
                state["rapport"]["cards"] = card_deck.new_cards(
                    card_deck.offer_from_picks(reply.rapport_cards), "conversation", turn,
                )
            complete_turn(state, plan, reply)

        # 모델 처리에 성공한 경우에만 메시지·상태·판정 로그를 한 트랜잭션으로 저장한다.
        conn.execute(
            "INSERT INTO messages (session_id, turn, role, content, request_id) VALUES (?, ?, 'user', ?, ?)",
            (session_id, turn, content, request.request_id),
        )
        message = insert_assistant(conn, session_id, turn, reply.content, reply.suggestions)
        save_state(conn, session_id, state, next_id)
        save_controller_event(conn, session_id, turn, decision_record(state, plan, reply))
        conn.commit()
        return {"message": message, "state": state}
    finally:
        conn.close()
        lock.release()
