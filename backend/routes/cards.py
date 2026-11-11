"""라포 카드: 챗봇이 정한 카드 한 장을 확인하고 진로 상담을 시작한다. 진로 상담 중에 알아가기를 다시 해 새 카드를 만든다."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from counseling import rapport
from counseling.controller import complete_turn, decision_record, plan_turn
from counseling.flow import RESTART_TEXT, enter_career
from llm.client import generate_reply
from routes.common import (
    acquire_session_lock, history_and_turn, insert_assistant, llm_errors, load_messages, load_state,
    require_pending_cards, require_session, save_controller_event, save_state,
)
from storage.db import get_db

router = APIRouter()


class CardChoiceRequest(BaseModel):
    card_id: int


# 카드 확인·알아가기 재시작 후 답변을 생성하고, UI 동작 기록·상태·판정을 한 번에 저장한다.
# 모델 또는 DB 오류가 나면 호출 라우트가 연결을 닫아 이번 변경을 모두 취소한다.
def _complete_card_turn(conn, session_id, state, next_id, history, turn, user_text, plan):
    with llm_errors():
        reply = generate_reply(state, history, user_text, turn, plan)
        rapport.apply_reply(state["rapport"], plan["rapport_action"], reply.asked_question_id)
        complete_turn(state, plan, reply)
    user_message_id = conn.execute(
        "INSERT INTO messages (session_id, turn, role, content) VALUES ($1, $2, 'user', $3) RETURNING id",
        (session_id, turn, user_text),
    ).fetchone()["id"]
    message = insert_assistant(conn, session_id, turn, reply.content, reply.suggestions)
    save_state(conn, session_id, state, next_id)
    save_controller_event(conn, session_id, turn, decision_record(state, plan, reply))
    conn.commit()
    return {
        "messages": [{"id": user_message_id, "turn": turn, "role": "user", "content": user_text}, message],
        "state": state,
    }


# 챗봇이 정한 카드를 배경정보로 남기고 진로 상담을 시작한다. 선택 기준이나 탐색 횟수는 자동 증가시키지 않는다.
@router.post("/api/sessions/{session_id}/cards/choose")
def choose_card(session_id: str, request: CardChoiceRequest):
    """카드를 배경정보로 기록한다. 아직 고민을 받지 않았다면 묻고, 이미 받았다면 그 고민을 구체화한다."""
    lock = acquire_session_lock(session_id)
    conn = None
    try:
        conn = get_db()
        require_session(conn, session_id)
        state, next_id = load_state(conn, session_id)
        cards = require_pending_cards(state)
        chosen = next((c for c in cards["offered"] if c["id"] == request.card_id), None)
        if chosen is None:
            raise HTTPException(status_code=422, detail="제시된 카드가 아닙니다.")

        history, turn = history_and_turn(load_messages(conn, session_id), state)

        cards["chosen_id"] = chosen["id"]
        cards["selected_turn"] = turn
        if state.get("career_started_turn") is not None:
            # 다시 한 알아가기의 새 카드를 확인하면 메시지 없이 이전 진로 상담으로 돌아간다.
            enter_career(state, turn)
            save_state(conn, session_id, state, next_id)
            conn.commit()
            return {"messages": [], "state": state}
        # 카드는 챗봇이 정했으므로 사용자가 고른 것처럼, 또는 진로 기준처럼 기록하지 않는다.
        user_text = "진로 상담을 시작할게요."
        plan = plan_turn(state, None, turn, card=chosen)
        return _complete_card_turn(conn, session_id, state, next_id, history, turn, user_text, plan)
    finally:
        if conn is not None:
            conn.close()
        lock.release()


# 진로 상담 중에 알아가기를 다시 시작한다. 새 질문 하나를 묻는 답변을 저장하고, 진로 상담은 새 카드를 확인할 때까지 멈춘다.
@router.post("/api/sessions/{session_id}/rapport/restart")
def restart_rapport(session_id: str):
    lock = acquire_session_lock(session_id)
    conn = None
    try:
        conn = get_db()
        require_session(conn, session_id)
        state, next_id = load_state(conn, session_id)
        if state["conversation_stage"] != "career" or state.get("career_started_turn") is None:
            raise HTTPException(status_code=422, detail="진로 상담 중에만 알아가기를 다시 할 수 있습니다.")
        rows = load_messages(conn, session_id)
        _, turn = history_and_turn(rows, state)
        plan = plan_turn(state, None, turn, restart=True)
        # 상태를 바꾼 뒤 기록을 만들어, 이번 알아가기 구간만 모델에 전달한다(아직 메시지가 없으므로 빈 기록).
        history, _ = history_and_turn(rows, state)
        return _complete_card_turn(conn, session_id, state, next_id, history, turn, RESTART_TEXT, plan)
    finally:
        if conn is not None:
            conn.close()
        lock.release()
