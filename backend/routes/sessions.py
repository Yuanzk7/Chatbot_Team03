"""상담 생성·조회."""
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from counseling.flow import INTRO_SUGGESTIONS
from counseling.state import empty_state
from routes.common import load_career_map, load_controller_events, load_messages, load_state, require_session
from storage.db import get_db

router = APIRouter()

GREETING = "안녕하세요, {name}님. 윤이입니다. 진로상담을 시작하기 전에 먼저 {name}님에 대해 알아가고 싶어요. 몇 가지 여쭤봐도 될까요?"


class SessionRequest(BaseModel):
    preferred_name: str
    # 실험용 상담별 설정. 기본 프롬프트를 유지하고 컨트롤러의 추가 통제 적용 여부만 비교한다.
    controller_enabled: bool = True


# 이름으로 상담을 만들고 통제 활성화 설정과 첫 인사를 함께 저장한다.
# controller_enabled는 상담별로 보관하므로 다른 상담의 실험 설정에 영향을 주지 않는다.
@router.post("/api/sessions")
def create_session(request: SessionRequest):
    """시작 화면에서 받은 이름으로 상담을 만들고, 윤이의 인사를 첫 메시지(turn 0)로 넣는다."""
    name = request.preferred_name.strip()
    if not name or len(name) > 20:
        raise HTTPException(status_code=422, detail="이름이나 호칭을 1~20자로 입력해 주세요.")
    session_id = str(uuid.uuid4())
    state = empty_state()
    state["preferred_name"] = name
    state["controller"]["enabled"] = request.controller_enabled
    suggestions = list(INTRO_SUGGESTIONS)
    content = GREETING.format(name=name)
    conn = get_db()
    try:
        conn.execute("INSERT INTO sessions (id) VALUES ($1)", (session_id,))
        conn.execute(
            "INSERT INTO session_state (session_id, state_json, next_item_id) VALUES ($1, $2, 1)",
            (session_id, json.dumps(state, ensure_ascii=False)),
        )
        message_id = conn.execute(
            "INSERT INTO messages (session_id, turn, role, content, suggestions) "
            "VALUES ($1, 0, 'assistant', $2, $3) RETURNING id",
            (session_id, content, json.dumps(suggestions, ensure_ascii=False)),
        ).fetchone()["id"]
        conn.commit()
    finally:
        conn.close()
    return {
        "session_id": session_id,
        "state": state,
        "message": {
            "id": message_id,
            "turn": 0,
            "role": "assistant",
            "content": content,
            "suggestions": suggestions,
        },
    }


# 새로고침 시 대화·상태·진로맵을 복원하고, 개발·평가용 턴별 통제 판정 기록도 반환한다.
@router.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    conn = get_db()
    try:
        require_session(conn, session_id)
        state, _ = load_state(conn, session_id)
        messages = load_messages(conn, session_id, state)
        return {
            "messages": messages,
            "state": state,
            "career_map": load_career_map(conn, session_id, state),
            "last_message_id": messages[-1]["id"] if messages else 0,
            "controller_events": load_controller_events(conn, session_id),
        }
    finally:
        conn.close()
