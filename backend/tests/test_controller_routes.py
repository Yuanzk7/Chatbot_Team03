"""실제 SQLite에 저장하되 모델 호출은 대체해, 통제 상태와 대화가 함께 저장되는지 검증한다."""
import json
import re
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling import cards as card_deck  # noqa: E402
from counseling.controller import ControlledReply  # noqa: E402
from counseling.flow import INTRO_SUGGESTIONS  # noqa: E402
from counseling.state import Classification  # noqa: E402
from llm import client  # noqa: E402
from llm.client import LLMResponseError  # noqa: E402
from routes import cards, messages, sessions  # noqa: E402
from routes.common import load_state, save_state  # noqa: E402
from storage import db  # noqa: E402


# 각 테스트를 임시 DB에서 실행해 실제 상담 기록을 건드리지 않는다.
@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "chatbot.db")
    db.init_db()


# 라포가 끝난 새 상담을 만들어 순수한 진로 탐색 턴의 저장 동작을 검증할 준비를 한다.
@pytest.fixture
def session_id(database):
    created = sessions.create_session(sessions.SessionRequest(preferred_name="테스트"))
    session_id = created["session_id"]
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state["rapport"].update(done=True, end_reason="completed", done_turn=0)
        state["conversation_stage"] = "career"
        save_state(conn, session_id, state, next_id)
    return session_id


# 기본 분류 결과를 만들고 테스트에 필요한 요청 유형·방향 신호만 덮어쓴다.
def classification(**overrides):
    data = {
        "preferred_name": None,
        "new_items": [],
        "updates": [],
        "rapport_category": None,
        "concern_tags": [],
        "rapport_answered_ids": [],
        "rapport_skipped": False,
        "wants_concern_first": False,
        "request_type": "reflection",
        "direction_relation": "new",
        "direction_label": "반도체 연구",
        "direction_choice": "none",
        "relevant_item_ids": [],
        "needs_reason": False,
        "follow_up": "reflect", "rapport_consent": False, "concern_provided": False,
    }
    data.update(overrides)
    return Classification.model_validate(data)


# 모델 대신 확정된 계획에 맞는 응답을 돌려주며, 실제 LLM API를 호출하지 않는다.
def controlled_reply(state, history, content, turn, plan):
    question = plan.get("fixed_question") or "어떤 경험이 가장 기억에 남나요?"
    return ControlledReply(content=f"함께 살펴보겠습니다.\n\n{question}", question=question)


# 기본 모델 대체 함수를 설치하고 호출 횟수를 확인할 수 있도록 mock을 반환한다.
def stub_models(monkeypatch, result=None):
    classify_mock = Mock(return_value=result or classification())
    reply_mock = Mock(side_effect=controlled_reply)
    monkeypatch.setattr(messages, "classify", classify_mock)
    monkeypatch.setattr(messages, "generate_reply", reply_mock)
    return classify_mock, reply_mock


# 정상 응답이 저장될 때 대화 두 개·방향 카운터·판정 로그가 함께 남는지 확인한다.
def test_message_saves_state_and_controller_event(session_id, monkeypatch):
    stub_models(monkeypatch)
    response = messages.send_message(
        session_id, messages.MessageRequest(content="반도체 연구를 더 알아보고 싶어요", request_id="turn-1"),
    )
    restored = sessions.get_session(session_id)
    assert restored["state"]["controller"]["direction"]["turns"] == 1
    assert restored["state"] == response["state"]
    assert len(restored["messages"]) == 3
    assert [event["turn"] for event in restored["controller_events"]] == [1]


# 같은 전송을 재시도해도 모델 재호출·방향 재계산·메시지와 로그 중복 저장이 없는지 확인한다.
def test_duplicate_request_returns_saved_reply_without_recounting(session_id, monkeypatch):
    classify_mock, reply_mock = stub_models(monkeypatch)
    request = messages.MessageRequest(content="반도체 연구를 알아보고 싶어요", request_id="same-request")
    first = messages.send_message(session_id, request)
    second = messages.send_message(session_id, request)
    restored = sessions.get_session(session_id)
    assert second["message"] == first["message"]
    assert restored["state"]["controller"]["direction"]["turns"] == 1
    assert len(restored["messages"]) == 3
    assert len(restored["controller_events"]) == 1
    assert classify_mock.call_count == reply_mock.call_count == 1


# 요청 ID가 같아도 내용이 다르면 이전 응답을 잘못 돌려주지 않고 충돌 오류를 내는지 확인한다.
def test_request_id_cannot_be_reused_for_different_content(session_id, monkeypatch):
    stub_models(monkeypatch)
    messages.send_message(session_id, messages.MessageRequest(content="첫 내용", request_id="same"))
    with pytest.raises(HTTPException) as error:
        messages.send_message(session_id, messages.MessageRequest(content="다른 내용", request_id="same"))
    assert error.value.status_code == 409
    assert len(sessions.get_session(session_id)["controller_events"]) == 1


# 분류 후 응답 생성이 실패해도 메모리에서 바뀐 상태가 DB에 먼저 저장되지 않는지 확인한다.
def test_model_failure_does_not_save_counter_messages_or_event(session_id, monkeypatch):
    stub_models(monkeypatch)
    before = sessions.get_session(session_id)
    monkeypatch.setattr(messages, "generate_reply", Mock(side_effect=LLMResponseError("실패")))
    with pytest.raises(HTTPException) as error:
        messages.send_message(session_id, messages.MessageRequest(content="탐색할래요", request_id="failed"))
    after = sessions.get_session(session_id)
    assert error.value.status_code == 502
    assert after["state"] == before["state"]
    assert after["messages"] == before["messages"]
    assert after["controller_events"] == []


# 마지막 로그 저장이 실패해도 같은 트랜잭션의 메시지·상태 저장까지 모두 취소되는지 확인한다.
def test_storage_failure_rolls_back_messages_and_state(session_id, monkeypatch):
    stub_models(monkeypatch)
    before = sessions.get_session(session_id)
    monkeypatch.setattr(messages, "save_controller_event", Mock(side_effect=sqlite3.OperationalError("실패")))
    with pytest.raises(sqlite3.OperationalError):
        messages.send_message(session_id, messages.MessageRequest(content="탐색할래요", request_id="failed-save"))
    after = sessions.get_session(session_id)
    assert after["state"] == before["state"]
    assert after["messages"] == before["messages"]
    assert after["controller_events"] == []


# 예전 상담에는 컨트롤러 키가 없어도 기본값으로 복원되며, 조회 자체는 원본 JSON을 바꾸지 않는다.
def test_old_session_is_backfilled_without_rewriting_on_read(session_id):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state.pop("controller")
        save_state(conn, session_id, state, next_id)
    restored = sessions.get_session(session_id)
    assert restored["state"]["controller"]["enabled"] is True
    assert restored["state"]["controller"]["direction"]["turns"] == 0
    with db.get_db() as conn:
        raw = conn.execute("SELECT state_json FROM session_state WHERE session_id = ?", (session_id,)).fetchone()
        assert "controller" not in json.loads(raw["state_json"])


# 적용 전후 비교 설정을 상담별로 저장해 다른 상담에는 영향을 주지 않는지 확인한다.
def test_controller_enabled_setting_is_per_session(database):
    disabled = sessions.create_session(sessions.SessionRequest(preferred_name="비교", controller_enabled=False))
    enabled = sessions.create_session(sessions.SessionRequest(preferred_name="기본"))
    assert sessions.get_session(disabled["session_id"])["state"]["controller"]["enabled"] is False
    assert sessions.get_session(enabled["session_id"])["state"]["controller"]["enabled"] is True


# 카드 선택도 통제를 거치되, 클릭을 대화 탐색 1턴으로 세지 않는지 확인한다.
def test_card_choice_applies_controller_without_incrementing_direction(session_id, monkeypatch):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        offered = card_deck.random_offer()
        state["rapport"]["cards"] = card_deck.new_cards(offered, "random", 0)
        state["controller"]["direction"].update(label="반도체 연구", turns=9)
        save_state(conn, session_id, state, next_id)
    reply_mock = Mock(side_effect=controlled_reply)
    monkeypatch.setattr(cards, "generate_reply", reply_mock)
    response = cards.choose_card(session_id, cards.CardChoiceRequest(card_id=offered[0]["id"]))
    plan = reply_mock.call_args.args[-1]
    assert plan["question"] == "career_concern"
    assert response["messages"][-1]["content"].endswith("지금 진로와 관련해서 어떤 고민이 있으신가요?")
    assert plan["allow_recommendation"] is False  # 카드만으로 경험이나 진로 기준을 확인하지 않는다.
    assert response["state"]["controller"]["direction"]["turns"] == 9
    assert response["state"]["criteria"] == []
    assert response["state"]["rapport"]["cards"]["chosen_id"] == offered[0]["id"]
    assert "criterion_item_id" not in response["state"]["rapport"]["cards"]
    assert len(sessions.get_session(session_id)["controller_events"]) == 1


# 실제 응답 생성·검증·저장을 연결해, 모델이 다른 질문을 내도 현재 고민 질문으로 상담을 시작한다.
@pytest.mark.parametrize("enabled", [True, False])
def test_card_choice_saves_fixed_current_concern_question_and_restores_state(session_id, monkeypatch, enabled):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        offered = card_deck.random_offer()
        state["rapport"]["cards"] = card_deck.new_cards(offered, "random", 0)
        state["controller"]["enabled"] = enabled
        state["controller"]["direction"].update(label="반도체 연구", turns=9, needs_clarification=True)
        state["constraints"].append({
            "id": next_id, "text": "졸업 직후 취업", "status": "unconfirmed", "turn": 0,
            "evidence": "아직 바로 취업할지는 모르겠어요",
        })
        state["controller"]["pending_confirmation_ids"] = [next_id]
        save_state(conn, session_id, state, next_id + 1)

    parse_mock = Mock(return_value=client.Reply(
        content="선택하신 카드를 참고하면서 이야기를 나눠볼게요.",
        question="그 기준이 중요했던 경험은 무엇인가요?",
        suggestions=["아직 잘 모르겠어요"],
        asked_question_id=None, rapport_cards=[], basis_ids=[], recommendations=[],
    ))
    monkeypatch.setattr(client, "_parse", parse_mock)
    response = cards.choose_card(session_id, cards.CardChoiceRequest(card_id=offered[0]["id"]))
    restored = sessions.get_session(session_id)
    expected = "지금 진로와 관련해서 어떤 고민이 있으신가요?"
    assert response["messages"][-1]["content"].endswith(expected)
    assert "그 기준이 중요했던 경험" not in response["messages"][-1]["content"]
    assert restored["messages"][-1] == response["messages"][-1]
    assert restored["state"]["controller"]["last_question"] == {
        "type": "career_concern", "item_ids": [], "text": expected,
    }
    assert restored["state"]["controller"]["direction"]["turns"] == 9
    assert restored["state"]["controller"]["direction"]["needs_clarification"] is True
    assert restored["state"]["controller"]["pending_confirmation_ids"] == [next_id]
    assert restored["state"]["criteria"] == []
    assert restored["state"]["rapport"]["cards"]["chosen_id"] == offered[0]["id"]
    assert offered[0]["meaning"] not in response["messages"][0]["content"]
    assert restored["controller_events"][-1]["decision"]["question"] == "career_concern"
    assert restored["controller_events"][-1]["decision"]["validation_errors"] == []
    assert parse_mock.call_count == 1
    assert parse_mock.call_args.args[-1] == "reply"


# 카드 응답 실패 시 선택 상태와 추가 기준까지 저장되지 않아 다시 고를 수 있는지 확인한다.
def test_card_model_failure_does_not_save_choice_or_criterion(session_id, monkeypatch):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        offered = card_deck.random_offer()
        state["rapport"]["cards"] = card_deck.new_cards(offered, "random", 0)
        save_state(conn, session_id, state, next_id)
    monkeypatch.setattr(cards, "generate_reply", Mock(side_effect=LLMResponseError("실패")))
    with pytest.raises(HTTPException) as error:
        cards.choose_card(session_id, cards.CardChoiceRequest(card_id=offered[0]["id"]))
    restored = sessions.get_session(session_id)
    assert error.value.status_code == 502
    assert restored["state"]["rapport"]["cards"]["chosen_id"] is None
    assert restored["state"]["criteria"] == []
    assert len(restored["messages"]) == 1
    assert restored["controller_events"] == []


# 실제 응답 생성·후검증 함수를 연결한 채 모델 출력만 대체해, 카드가 섞여도 실제 탐색 10턴에 질문하는지 검증한다.
def test_scripted_conversation_counts_exploration_and_restores_checkpoint(session_id, monkeypatch):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        offered = card_deck.random_offer()
        state["rapport"]["cards"] = card_deck.new_cards(offered, "random", 0)
        save_state(conn, session_id, state, next_id)

    # 분류에는 실제 스키마 객체를, 응답에는 일부러 다른 질문을 넣어 코드의 고정 질문 조립까지 확인한다.
    def scripted_parse(instructions, history, text_format, label):
        if label == "classify":
            content = history[-1]["content"]
            if content == "이 방향으로 더 탐색할게요":
                return classification(direction_relation="none", direction_label=None, direction_choice="continue")
            relation = "new" if content == "반도체 연구를 탐색할래요" else "same"
            return classification(direction_relation=relation)
        assert label == "reply"
        assert text_format is client.Reply
        return client.Reply(
            content="말씀하신 내용을 함께 살펴보겠습니다.",
            question="이 경험에서 가장 흥미로웠던 점은 무엇인가요?",
            suggestions=["계속 알아보고 싶어요", "아직 잘 모르겠어요"],
            asked_question_id=None,
            rapport_cards=[],
            basis_ids=[],
            recommendations=[],
        )

    parse_mock = Mock(side_effect=scripted_parse)
    monkeypatch.setattr(client, "_parse", parse_mock)
    first = messages.send_message(
        session_id, messages.MessageRequest(content="반도체 연구를 탐색할래요", request_id="script-1"),
    )
    assert first["state"]["controller"]["direction"]["turns"] == 1

    picked = cards.choose_card(session_id, cards.CardChoiceRequest(card_id=offered[0]["id"]))
    assert picked["state"]["controller"]["direction"]["turns"] == 1
    assert picked["messages"][-1]["turn"] == 2

    for exploration_turn in range(2, 11):
        response = messages.send_message(
            session_id,
            messages.MessageRequest(content=f"반도체 연구 이야기를 이어갈게요 {exploration_turn}",
                                    request_id=f"script-{exploration_turn}"),
        )
        assert response["state"]["controller"]["direction"]["turns"] == exploration_turn
        assert response["state"]["controller"]["direction"]["awaiting_choice"] is (exploration_turn == 10)

    checkpoint = "‘반도체 연구’에 대해 10턴 동안 탐색했어요. 이 방향을 계속 탐색할까요, 아니면 다른 가능성을 살펴볼까요?"
    assert response["message"]["turn"] == 11  # 카드 선택 때문에 메시지 번호는 탐색 횟수보다 하나 크다.
    assert response["message"]["content"].endswith(checkpoint)
    assert "이 경험에서 가장 흥미로웠던 점은 무엇인가요?" not in response["message"]["content"]

    restored = sessions.get_session(session_id)
    assert restored["state"]["controller"]["direction"]["awaiting_choice"] is True
    assert restored["state"]["controller"]["direction"]["turns"] == 10
    assert restored["messages"][-1]["content"].endswith(checkpoint)
    assert restored["controller_events"][-1]["decision"]["question"] == "direction_check"
    assert restored["controller_events"][-1]["decision"]["validation_errors"] == []

    continued = messages.send_message(
        session_id, messages.MessageRequest(content="이 방향으로 더 탐색할게요", request_id="script-continue"),
    )
    assert continued["state"]["controller"]["direction"]["label"] == "반도체 연구"
    assert continued["state"]["controller"]["direction"]["turns"] == 0
    assert continued["state"]["controller"]["direction"]["awaiting_choice"] is False
    assert continued["message"]["turn"] == 12
    assert parse_mock.call_count == 23  # 일반 대화 11회×분류·응답 + 카드 응답 1회.


# 라포 제어 테스트에서는 진로 방향을 임의로 만들지 않고 필요한 신호만 추가한다.
def flow_classification(**changes):
    fields = dict(direction_relation="none", direction_label=None)
    fields.update(changes)
    return classification(**fields)


# 실제 분류·응답 함수와 후검증을 사용하되, 유료 모델의 출력만 순서대로 대체한다.
def scripted_models(monkeypatch, results):
    pending = iter(results)

    # 현재 프롬프트가 지시한 라포 질문 ID만 보고하며, 후보는 일부러 세 개 반환해 서버 차단을 검증한다.
    def parse(instructions, history, schema, label):
        if label == "classify":
            return next(pending)
        asked = re.search(r"\(id (\d+)\)", instructions)
        return client.Reply(content="말씀하신 내용을 함께 살펴볼게요.",
                            question="지금 가장 고민되는 점은 무엇인가요?",
                            suggestions=["첫 번째 답", "두 번째 답", "잘 모르겠어요"],
                            asked_question_id=int(asked.group(1)) if asked else None,
                            rapport_cards=[], basis_ids=[], recommendations=[])

    mock = Mock(side_effect=parse)
    monkeypatch.setattr(client, "_parse", mock)
    return mock


# 새 상담에는 두 선택지만 있고 동의 후에는 실제 DB 저장·복원에서도 라포 후보가 없어야 한다.
def test_intro_and_consent_persist_free_text_rapport(database, monkeypatch):
    created = sessions.create_session(sessions.SessionRequest(preferred_name="유찬"))
    assert created["message"]["suggestions"] == INTRO_SUGGESTIONS
    assert created["state"]["conversation_stage"] == "intro"
    scripted_models(monkeypatch, [flow_classification(rapport_consent=True)])
    response = messages.send_message(created["session_id"], messages.MessageRequest(
        content="네, 여쭤보세요", request_id="consent"))
    restored = sessions.get_session(created["session_id"])
    assert response["state"]["conversation_stage"] == "rapport"
    assert response["message"]["suggestions"] == []
    assert restored["messages"][-1]["suggestions"] == []
    with db.get_db() as conn:
        stored = conn.execute("SELECT suggestions FROM messages WHERE id = ?", (response["message"]["id"],)).fetchone()
        assert json.loads(stored["suggestions"]) == []


# 첫 안내에서 바로 상담을 원하면 카드 없이 현재 고민을 묻는 답변을 저장한다.
def test_skip_intro_enters_counseling_without_offering_cards(database, monkeypatch):
    created = sessions.create_session(sessions.SessionRequest(preferred_name="유찬"))
    parsed = scripted_models(monkeypatch, [flow_classification(wants_concern_first=True)])
    response = messages.send_message(created["session_id"], messages.MessageRequest(
        content="바로 고민부터 말할게요", request_id="direct-career"))
    assert response["state"]["conversation_stage"] == "career"
    assert response["state"]["rapport"]["cards"] is None
    assert response["message"]["content"].endswith("지금 진로와 관련해서 어떤 고민이 있으신가요?")
    assert "카드 선택을 짧게 받아들이" not in parsed.call_args.args[0]


# 라포 중 진로 고민을 함께 말하며 중단하면 일반적인 고민 질문을 다시 하지 않고 그 고민부터 다룬다.
def test_stop_rapport_with_actual_concern_does_not_reask_it(database, monkeypatch):
    created = sessions.create_session(sessions.SessionRequest(preferred_name="유찬"))
    scripted_models(monkeypatch, [flow_classification(rapport_consent=True),
                                 flow_classification(wants_concern_first=True, concern_provided=True,
                                                     request_type="information")])
    messages.send_message(created["session_id"], messages.MessageRequest(content="네", request_id="start"))
    response = messages.send_message(created["session_id"], messages.MessageRequest(
        content="질문은 그만하고 연구와 실무 중 어떤 길이 좋을지 이야기하고 싶어요", request_id="stop-rapport"))
    assert response["state"]["conversation_stage"] == "career"
    assert response["state"]["rapport"]["cards"] is None
    assert all(q["status"] == "skipped" for q in response["state"]["rapport"]["questions"])
    assert response["state"]["controller"]["last_question"]["type"] == "reflect_after_answer"
    assert "지금 진로와 관련해서 어떤 고민이 있으신가요?" not in response["message"]["content"]
    assert response["message"]["suggestions"]


# 이미 제시된 카드를 건너뛰면 다음 상담 질문까지 저장하고, UI에 추가할 메시지 두 개를 반환한다.
def test_card_dismiss_returns_next_question_and_restores_it(session_id, monkeypatch):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state["conversation_stage"] = "cards"
        state["rapport"]["cards"] = card_deck.new_cards(card_deck.random_offer(), "random", 0)
        save_state(conn, session_id, state, next_id)
    scripted_models(monkeypatch, [])
    response = cards.dismiss_cards(session_id)
    restored = sessions.get_session(session_id)
    assert len(response["messages"]) == 2
    assert response["messages"][-1]["content"].endswith("지금 진로와 관련해서 어떤 고민이 있으신가요?")
    assert response["state"]["rapport"]["cards"]["dismissed"] is True
    assert response["state"]["conversation_stage"] == "career"
    assert response["state"]["criteria"] == []
    assert restored["messages"][-1] == response["messages"][-1]
    assert len(restored["controller_events"]) == 1


# 건너뛰기 응답을 받지 못한 경우에도 카드를 닫은 상태만 먼저 저장되지 않는다.
def test_card_dismiss_failure_rolls_back_transition(session_id, monkeypatch):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state["conversation_stage"] = "cards"
        state["rapport"]["cards"] = card_deck.new_cards(card_deck.random_offer(), "random", 0)
        save_state(conn, session_id, state, next_id)
    before = sessions.get_session(session_id)
    monkeypatch.setattr(cards, "generate_reply", Mock(side_effect=LLMResponseError("실패")))
    with pytest.raises(HTTPException):
        cards.dismiss_cards(session_id)
    assert sessions.get_session(session_id) == before


# 이전 버전에 저장된 라포 후보 세 개도 새로고침하면 감추고, DB 원문은 조회만으로 수정하지 않는다.
def test_legacy_rapport_suggestions_are_hidden_on_restore(session_id):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state.pop("conversation_stage")
        state["rapport"].update(done=False, done_turn=None, questions=[
            {"id": 17, "status": "asked", "text": "경험", "asks": 1}])
        save_state(conn, session_id, state, next_id)
        conn.execute("UPDATE messages SET suggestions = ? WHERE session_id = ?",
                     (json.dumps(["기존 답변1", "기존 답변2", "기존 답변3"]), session_id))
    restored = sessions.get_session(session_id)
    assert restored["state"]["conversation_stage"] == "rapport"
    assert restored["messages"][-1]["suggestions"] == []
    with db.get_db() as conn:
        raw = conn.execute("SELECT suggestions FROM messages WHERE session_id = ?", (session_id,)).fetchone()
        assert len(json.loads(raw["suggestions"])) == 3


# ID 연결 전의 카드 자동 기준도 선택 메시지의 턴으로 식별해 복원된 기준·진로맵에서 제외한다.
def test_legacy_card_criterion_and_map_are_filtered_without_rewriting_database(session_id):
    card = card_deck.DECK_BY_ID[12]
    with db.get_db() as conn:
        state, _ = load_state(conn, session_id)
        state.pop("conversation_stage")
        state["rapport"]["cards"] = {**card_deck.new_cards([card], "conversation", 1), "chosen_id": 12}
        state["criteria"] = [dict(id=1, text=card["criterion"], status="confirmed", turn=2, evidence="원본 발언")]
        save_state(conn, session_id, state, 2)
        conn.execute("INSERT INTO messages (session_id, turn, role, content) VALUES (?, 2, 'user', ?)",
                     (session_id, f"'{card['name']}' 카드를 골랐어요. ({card['meaning']})"))
        career_map = {"criteria": [{"id": 1, "text": card["criterion"], "status": "confirmed"}],
                      "candidates": [{"name": "연구", "source": "user", "by_criteria": [{"criterion_id": 1, "note": "비교"}]}],
                      "next_actions": [], "based_on_message_id": 1, "updated_at": "2026-11-02T00:00:00Z"}
        conn.execute("INSERT INTO career_maps (session_id, map_json, based_on_message_id) VALUES (?, ?, 1)",
                     (session_id, json.dumps(career_map)))
    restored = sessions.get_session(session_id)
    assert restored["state"]["criteria"][0]["source"] == "rapport_card"
    assert restored["career_map"]["criteria"] == []
    assert restored["career_map"]["candidates"][0]["by_criteria"] == []
    with db.get_db() as conn:
        raw = json.loads(conn.execute("SELECT state_json FROM session_state WHERE session_id = ?", (session_id,)).fetchone()[0])
        assert "source" not in raw["criteria"][0]
        assert "conversation_stage" not in raw


# 정보 요청에도 성찰 질문을 이어가되, 설명만 요청하거나 대화를 끝내려는 의도는 별도로 존중한다.
@pytest.mark.parametrize("follow_up,has_question", [("reflect", True), ("answer_only", False), ("close", False)])
def test_information_follow_up_intent_survives_full_route(session_id, monkeypatch, follow_up, has_question):
    scripted_models(monkeypatch, [flow_classification(request_type="information", follow_up=follow_up)])
    response = messages.send_message(session_id, messages.MessageRequest(content="연구와 실무를 비교해 보고 싶어요", request_id="compare"))
    restored = sessions.get_session(session_id)
    assert bool(response["state"]["controller"]["last_question"]) is has_question
    assert ("?" in response["message"]["content"]) is has_question
    assert bool(restored["messages"][-1]["suggestions"]) is has_question
