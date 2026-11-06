"""라우트들이 함께 쓰는 것: 상담별 lock, 상담·상태·메시지 읽기/쓰기, 모델 오류 변환."""
import json
import threading
from contextlib import contextmanager

import openai
from fastapi import HTTPException

from counseling import cards as card_deck
from counseling.controller import ensure_controller
from counseling.exploration import ensure_exploration
from counseling.flow import ensure_flow, history_for_model, suggestions_for_stage, without_card_criteria
from counseling.rapport import empty_rapport
from counseling.state import empty_state
from llm.client import LLMConfigError, LLMResponseError

# 상담별 lock. 같은 상담에 전송·정리·카드 요청이 겹치면 409를 반환한다
_locks = {}
_locks_guard = threading.Lock()


# 같은 상담에 메시지·카드·정리 요청이 동시에 들어오면 하나만 허용하고 나머지는 409로 돌려준다.
def acquire_session_lock(session_id):
    with _locks_guard:
        lock = _locks.setdefault(session_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="이전 요청을 처리하고 있습니다. 잠시 후 다시 시도해 주세요.")
    return lock


# 전달받은 상담 ID가 DB에 있는지 검사해 다른 처리 전에 잘못된 요청을 404로 중단한다.
def require_session(conn, session_id):
    row = conn.execute("SELECT 1 FROM sessions WHERE id = $1", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="상담을 찾을 수 없습니다.")


# 저장된 상담 상태를 읽고 이전 버전 상담에 빠진 라포·컨트롤러 기본값을 메모리에서 보완한다.
# 실제 저장은 요청 처리 성공 후 save_state가 맡으므로 조회만으로 기존 DB를 바꾸지 않는다.
def load_state(conn, session_id):
    """(state, next_item_id). 아직 없으면 빈 상태."""
    row = conn.execute(
        "SELECT state_json, next_item_id FROM session_state WHERE session_id = $1", (session_id,)
    ).fetchone()
    if row:
        state = json.loads(row["state_json"])
        state.setdefault("rapport", empty_rapport())  # 이전 버전 상담에는 rapport가 없다
        ensure_controller(state)
        cards = state["rapport"].get("cards")
        if cards and cards.get("chosen_id") and "selected_turn" not in cards and "criterion_item_id" not in cards:
            chosen = next((c for c in cards["offered"] if c["id"] == cards["chosen_id"]), None)
            if chosen:
                old_text = f"'{chosen['name']}' 카드를 골랐어요. ({chosen.get('meaning', '')})"
                selection = conn.execute(
                    "SELECT turn FROM messages WHERE session_id = $1 AND role = 'user' AND content = $2",
                    (session_id, old_text),
                ).fetchone()
                if selection:
                    cards["selected_turn"] = selection["turn"]
        ensure_flow(state)
        legacy_actions = []
        if "exploration" not in state:
            old_map = conn.execute("SELECT map_json FROM career_maps WHERE session_id = $1", (session_id,)).fetchone()
            if old_map:
                legacy_actions = json.loads(old_map["map_json"]).get("next_actions", [])
        ensure_exploration(state, legacy_actions)
        return state, row["next_item_id"]
    return empty_state(), 1


# 상담 상태와 다음 항목 ID를 저장한다. 실제 commit은 메시지·로그까지 준비한 라우트가 수행한다.
def save_state(conn, session_id, state, next_id):
    conn.execute(
        "INSERT INTO session_state (session_id, state_json, next_item_id) VALUES ($1, $2, $3) "
        "ON CONFLICT(session_id) DO UPDATE SET state_json = excluded.state_json, "
        "next_item_id = excluded.next_item_id, updated_at = CURRENT_TIMESTAMP",
        (session_id, json.dumps(state, ensure_ascii=False), next_id),
    )


# 한 턴에서 적용된 규칙과 검증 결과를 기록한다. commit은 호출한 라우트가 맡는다.
# 메시지·상태와 같은 트랜잭션에 넣어 응답만 저장되거나 카운터만 바뀌는 상황을 막는다.
def save_controller_event(conn, session_id, turn, event):
    conn.execute(
        "INSERT INTO controller_events (session_id, turn, event_json) VALUES ($1, $2, $3)",
        (session_id, turn, json.dumps(event, ensure_ascii=False)),
    )


# 상담별 판정 로그를 턴 순서대로 반환해 동일 프롬프트의 통제 적용 전후를 비교할 수 있게 한다.
def load_controller_events(conn, session_id):
    rows = conn.execute(
        "SELECT turn, event_json, created_at FROM controller_events WHERE session_id = $1 ORDER BY turn",
        (session_id,),
    ).fetchall()
    return [
        {"turn": row["turn"], "decision": json.loads(row["event_json"]), "created_at": row["created_at"]}
        for row in rows
    ]


# 저장된 진로맵 JSON을 읽는다. 아직 정리하지 않은 상담이면 None을 반환한다.
def load_career_map(conn, session_id, state=None):
    row = conn.execute("SELECT map_json FROM career_maps WHERE session_id = $1", (session_id,)).fetchone()
    career_map = json.loads(row["map_json"]) if row else None
    return without_card_criteria(career_map, state) if state is not None else career_map


# DB 행을 화면에 전달할 메시지로 바꾸고, 챗봇 메시지에 저장된 예시 답변도 복원한다.
def message_dict(row):
    m = {"id": row["id"], "turn": row["turn"], "role": row["role"], "content": row["content"]}
    if row["role"] == "assistant":
        m["suggestions"] = json.loads(row["suggestions"]) if row["suggestions"] else []
    return m


# 상담의 메시지를 저장 순서대로 읽어 화면 복원과 모델 대화 기록 구성에 사용한다.
def load_messages(conn, session_id, state=None):
    rows = conn.execute(
        "SELECT id, turn, role, content, suggestions FROM messages WHERE session_id = $1 ORDER BY id",
        (session_id,),
    ).fetchall()
    messages = [message_dict(r) for r in rows]
    if state is not None:
        for message in messages:
            if message["role"] == "assistant":
                has_question = (message["turn"] == 0 or bool(message["suggestions"])
                                or bool(state["controller"]["last_question"]))
                message["suggestions"] = suggestions_for_stage(state, message["suggestions"], has_question)
    return messages


# 모델에 보낼 역할·본문 목록과 다음 대화 번호를 만든다. 방향별 탐색 횟수는 컨트롤러가 따로 센다.
def history_and_turn(rows, state=None):
    """모델에 보낼 대화 기록과 이번 턴 번호(사용자 메시지 수 + 1)."""
    history = history_for_model(rows, state) if state is not None else [
        {"role": r["role"], "content": r["content"]} for r in rows]
    turn = sum(1 for r in rows if r["role"] == "user") + 1
    return history, turn


# 검증된 챗봇 답변·예시 답변을 저장하고, 화면에 바로 보낼 메시지 객체를 반환한다.
def insert_assistant(conn, session_id, turn, content, suggestions):
    message_id = conn.execute(
        "INSERT INTO messages (session_id, turn, role, content, suggestions) "
        "VALUES ($1, $2, 'assistant', $3, $4) RETURNING id",
        (session_id, turn, content, json.dumps(suggestions, ensure_ascii=False)),
    ).fetchone()["id"]
    return {"id": message_id, "turn": turn, "role": "assistant", "content": content, "suggestions": suggestions}


# 현재 선택 가능한 카드가 있는지 검사해 이미 선택·건너뛴 카드의 중복 처리를 막는다.
def require_pending_cards(state):
    cards = state["rapport"].get("cards")
    if not card_deck.pending(cards):
        raise HTTPException(status_code=422, detail="지금 고를 수 있는 카드가 없습니다.")
    return cards


# 모델 설정·인증·생성 오류를 사용자가 이해할 수 있는 HTTP 안내로 변환한다.
# 호출자가 추가한 상태 병합 오류도 같은 경계에서 처리하며, 실패한 턴의 저장은 진행하지 않는다.
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
