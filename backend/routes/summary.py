"""'현재까지 정리하기': 선택 기준·후보 비교·다음 탐색 행동을 정리해 career_maps에 덮어쓴다."""
import json

from fastapi import APIRouter, HTTPException

from counseling.career_map import CareerMapError, build_career_map
from counseling.exploration import record_summary
from llm.client import summarize
from routes.common import acquire_session_lock, history_and_turn, llm_errors, load_messages, load_state, require_session, save_state
from storage.db import get_db

router = APIRouter()


# 대화로 확인한 기준으로 진로맵을 만든다. 카드 선택만으로 생성된 과거 기준은 요약 입력에서 제외한다.
@router.post("/api/sessions/{session_id}/summary")
def summarize_session(session_id: str):
    lock = acquire_session_lock(session_id)
    conn = None
    try:
        conn = get_db()
        require_session(conn, session_id)
        rows = load_messages(conn, session_id)
        if not any(r["role"] == "user" for r in rows):
            raise HTTPException(status_code=422, detail="정리할 대화가 아직 없습니다. 먼저 대화를 나눠 주세요.")
        state, next_id = load_state(conn, session_id)
        history, turn = history_and_turn(rows, state)

        with llm_errors("정리 결과를 받지 못했습니다. 잠시 후 다시 시도해 주세요.", extra=(CareerMapError,)):
            output = summarize(state, history)
            career_map = build_career_map(state, output, rows[-1]["id"])
            record_summary(state, career_map["next_actions"], turn - 1)

        conn.execute(
            "INSERT INTO career_maps (session_id, map_json, based_on_message_id) VALUES ($1, $2, $3) "
            "ON CONFLICT(session_id) DO UPDATE SET map_json = excluded.map_json, "
            "based_on_message_id = excluded.based_on_message_id, updated_at = CURRENT_TIMESTAMP",
            (session_id, json.dumps(career_map, ensure_ascii=False), career_map["based_on_message_id"]),
        )
        save_state(conn, session_id, state, next_id)
        conn.commit()
        return {"career_map": career_map}
    finally:
        if conn is not None:
            conn.close()
        lock.release()
