"""선택한 테스트 DB에 저장하되 모델 호출은 대체해, 통제 상태와 대화가 함께 저장되는지 검증한다."""
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
from counseling import exploration  # noqa: E402
from counseling.career_map import CareerMapOutput  # noqa: E402
from counseling.controller import ControlledReply  # noqa: E402
from counseling.state import Classification  # noqa: E402
from llm import client  # noqa: E402
from llm.client import LLMResponseError  # noqa: E402
from routes import cards, messages, sessions, summary  # noqa: E402
from routes.common import acquire_session_lock, load_state, save_state  # noqa: E402
from storage import db  # noqa: E402


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


# DB 접속이 실패해도 상담 잠금이 풀려, 다음 요청이 계속 409로 막히지 않는다.
@pytest.mark.parametrize("module,handler,kwargs", [
    (messages, messages.send_message, {"request": messages.MessageRequest(content="상담", request_id="outage")}),
    (summary, summary.summarize_session, {}),
    (cards, cards.choose_card, {"request": cards.CardChoiceRequest(card_id=1)}),
    (cards, cards.restart_rapport, {}),
])
def test_database_connection_failure_releases_session_lock(monkeypatch, module, handler, kwargs):
    session_id = "db-outage-" + handler.__name__
    monkeypatch.setattr(module, "get_db", Mock(side_effect=ConnectionError("일시적인 연결 실패")))
    with pytest.raises(ConnectionError):
        handler(session_id, **kwargs)
    lock = acquire_session_lock(session_id)
    lock.release()


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
        raw = conn.execute("SELECT state_json FROM session_state WHERE session_id = $1", (session_id,)).fetchone()
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


# 새 상담은 동의 단계 없이 첫 인사에서 기본 질문을 묻고, 답한 뒤에도 라포 후보 답변을 저장·표시하지 않는다.
def test_new_session_asks_first_rapport_question_without_suggestions(database, monkeypatch):
    created = sessions.create_session(sessions.SessionRequest(preferred_name="유찬"))
    assert created["message"]["suggestions"] == []
    assert created["message"]["content"].endswith("지금까지 해본 것 중에 가장 재미있었던 경험은 무엇인가요?")
    assert created["state"]["conversation_stage"] == "rapport"
    assert [(q["id"], q["status"]) for q in created["state"]["rapport"]["questions"]] == [(17, "asked")]
    scripted_models(monkeypatch, [flow_classification(rapport_answered_ids=[17], rapport_category="taste",
                                                      concern_tags=["interest"])])
    response = messages.send_message(created["session_id"], messages.MessageRequest(
        content="유럽여행을 한 달 동안 다녀온 게 제일 재밌었어요", request_id="first-answer"))
    restored = sessions.get_session(created["session_id"])
    assert response["state"]["conversation_stage"] == "rapport"
    assert response["state"]["rapport"]["questions"][0]["status"] == "answered"
    assert response["message"]["suggestions"] == []
    assert restored["messages"][-1]["suggestions"] == []
    with db.get_db() as conn:
        stored = conn.execute("SELECT suggestions FROM messages WHERE id = $1", (response["message"]["id"],)).fetchone()
        assert json.loads(stored["suggestions"]) == []


# 알아가기 중에 바로 상담하자고 재촉하면 남은 질문을 줄여 카드 한 장을 보여주고, 진로 상담은 카드 확인 뒤에 시작한다.
def test_request_to_skip_rapport_shows_card_before_counseling(database, monkeypatch):
    created = sessions.create_session(sessions.SessionRequest(preferred_name="유찬"))
    scripted_models(monkeypatch, [flow_classification(wants_concern_first=True, concern_provided=True,
                                                      request_type="information")])
    response = messages.send_message(created["session_id"], messages.MessageRequest(
        content="질문은 그만하고 연구와 실무 중 어떤 길이 좋을지 이야기하고 싶어요", request_id="skip-rapport"))
    state = response["state"]
    assert state["conversation_stage"] == "cards"
    assert state["rapport"]["end_reason"] == "user_requested"
    assert len(state["rapport"]["cards"]["offered"]) == 1
    assert state["career_concern_received"] is True
    assert "career_started_turn" not in state
    assert response["message"]["suggestions"] == []


# 진로 상담 중에 알아가기를 다시 하면 새 질문으로 시작하고, 새 카드를 확인하면 메시지 없이 진로 상담으로 돌아간다.
def test_restart_rapport_then_confirm_new_card_returns_to_career(session_id, monkeypatch):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state["career_started_turn"] = 1
        state["rapport"]["questions"] = [{"id": 17, "text": "경험", "status": "answered", "asks": 1, "holds": 0}]
        state["rapport"]["cards"] = {**card_deck.new_cards([card_deck.DECK_BY_ID[6]], "conversation", 0), "chosen_id": 6}
        save_state(conn, session_id, state, next_id)
    scripted_models(monkeypatch, [])
    restarted = cards.restart_rapport(session_id)
    state = restarted["state"]
    assert [m["content"] for m in restarted["messages"][:1]] == ["알아가기를 다시 할게요."]
    assert state["conversation_stage"] == "rapport" and state["career_started_turn"] == 1
    assert state["room_switches"][-1] == {"room": "rapport", "turn": restarted["messages"][0]["turn"]}
    assert 17 not in {q["id"] for q in state["rapport"]["questions"]}
    assert any(q["status"] == "asked" for q in state["rapport"]["questions"])
    assert state["rapport"]["previous_card"]["id"] == 6
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state["conversation_stage"] = "cards"
        state["rapport"].update(done=True, done_turn=2, end_reason="completed")
        state["rapport"]["cards"] = card_deck.new_cards([card_deck.DECK_BY_ID[2]], "conversation", 2)
        save_state(conn, session_id, state, next_id)
    before = len(sessions.get_session(session_id)["messages"])
    returned = cards.choose_card(session_id, cards.CardChoiceRequest(card_id=2))
    assert returned["messages"] == []
    assert returned["state"]["conversation_stage"] == "career"
    assert returned["state"]["room_switches"][-1]["room"] == "career"
    assert returned["state"]["rapport"]["cards"]["chosen_id"] == 2
    assert len(sessions.get_session(session_id)["messages"]) == before


# 진로 상담이 시작되기 전에는 알아가기를 다시 시작할 수 없다.
def test_restart_rapport_requires_career_stage(database):
    created = sessions.create_session(sessions.SessionRequest(preferred_name="유찬"))
    with pytest.raises(HTTPException) as error:
        cards.restart_rapport(created["session_id"])
    assert error.value.status_code == 422


# 이전 버전에 저장된 라포 후보 세 개도 새로고침하면 감추고, DB 원문은 조회만으로 수정하지 않는다.
def test_legacy_rapport_suggestions_are_hidden_on_restore(session_id):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state.pop("conversation_stage")
        state["rapport"].update(done=False, done_turn=None, questions=[
            {"id": 17, "status": "asked", "text": "경험", "asks": 1}])
        save_state(conn, session_id, state, next_id)
        conn.execute("UPDATE messages SET suggestions = $1 WHERE session_id = $2",
                     (json.dumps(["기존 답변1", "기존 답변2", "기존 답변3"]), session_id))
    restored = sessions.get_session(session_id)
    assert restored["state"]["conversation_stage"] == "rapport"
    assert restored["messages"][-1]["suggestions"] == []
    with db.get_db() as conn:
        raw = conn.execute("SELECT suggestions FROM messages WHERE session_id = $1", (session_id,)).fetchone()
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
        conn.execute("INSERT INTO messages (session_id, turn, role, content) VALUES ($1, 2, 'user', $2)",
                     (session_id, f"'{card['name']}' 카드를 골랐어요. ({card['meaning']})"))
        career_map = {"criteria": [{"id": 1, "text": card["criterion"], "status": "confirmed"}],
                      "candidates": [{"name": "연구", "source": "user", "by_criteria": [{"criterion_id": 1, "note": "비교"}]}],
                      "next_actions": [], "based_on_message_id": 1, "updated_at": "2026-11-02T00:00:00Z"}
        conn.execute("INSERT INTO career_maps (session_id, map_json, based_on_message_id) VALUES ($1, $2, 1)",
                     (session_id, json.dumps(career_map)))
    restored = sessions.get_session(session_id)
    assert restored["state"]["criteria"][0]["source"] == "rapport_card"
    assert restored["career_map"]["criteria"] == []
    assert restored["career_map"]["candidates"][0]["by_criteria"] == []
    with db.get_db() as conn:
        raw = json.loads(conn.execute("SELECT state_json FROM session_state WHERE session_id = $1", (session_id,)).fetchone()["state_json"])
        assert "source" not in raw["criteria"][0]
        assert "conversation_stage" not in raw


# 모델이 필요한 후속 질문을 골라도 명시적인 질문 생략·종료 요청은 우선한다.
@pytest.mark.parametrize("follow_up,has_question", [("reflect", True), ("answer_only", False), ("close", False)])
def test_information_follow_up_intent_survives_full_route(session_id, monkeypatch, follow_up, has_question):
    scripted_models(monkeypatch, [flow_classification(request_type="information", follow_up=follow_up)])
    response = messages.send_message(session_id, messages.MessageRequest(content="연구와 실무를 비교해 보고 싶어요", request_id="compare"))
    restored = sessions.get_session(session_id)
    assert bool(response["state"]["controller"]["last_question"]) is has_question
    assert ("?" in response["message"]["content"]) is has_question
    assert bool(restored["messages"][-1]["suggestions"]) is has_question


# 일반 상담에서 모델이 질문 없이 충분히 답하면 저장·복원 과정에서도 임의의 질문을 붙이지 않는다.
def test_information_without_question_survives_save_and_restore(session_id, monkeypatch):
    content = "데이터 구성과 분석 방법을 함께 살펴보면 연구 질문을 검증하는 과정을 이해할 수 있어요."
    parsed = Mock(side_effect=[flow_classification(request_type="information"),
                               client.Reply(content=content, question=None, suggestions=["데이터부터"],
                                            asked_question_id=None, rapport_cards=[], basis_ids=[], recommendations=[])])
    monkeypatch.setattr(client, "_parse", parsed)
    response = messages.send_message(session_id, messages.MessageRequest(
        content="데이터와 분석 방법", request_id="explain-both"))
    restored = sessions.get_session(session_id)
    assert response["message"]["content"] == content
    assert restored["messages"][-1]["content"] == content
    assert restored["messages"][-1]["suggestions"] == []
    assert restored["state"]["controller"]["last_question"] is None
    assert restored["controller_events"][-1]["decision"]["validation_errors"] == []


# 진행 분류에 필요한 무변경 필드를 채워 테스트가 실제 API 스키마를 사용하도록 한다.
def exploration_signals(**changes):
    fields = dict(concern=None, focus=None, conditions="unchanged", defer=None, candidates=[], actions=[])
    fields.update(changes)
    return fields


# 제안 → 명시적 선택 → 진로맵까지 동일한 행동 ID와 사용자 선택을 저장·복원한다.
def test_exploration_decision_survives_message_summary_and_restore(session_id, monkeypatch):
    first = classification(direction_relation="new", direction_label="대학원",
                           exploration=exploration_signals(concern="석사에서 무엇을 배울까", focus="action"))
    decision = "이번 주에 연구실 논문을 찾아볼게요"
    second = classification(direction_relation="same", direction_label=None,
                            exploration=exploration_signals(actions=[dict(action_id=1, text=None,
                                                       status="selected", evidence=decision)]))
    proposed = client.Reply(content="배움을 확인할 작은 탐색을 해볼 수 있어요.", question=None,
                            suggestions=[], asked_question_id=None, rapport_cards=[], basis_ids=[], recommendations=[],
                            exploration=dict(compared_candidates=[], proposed_actions=["연구실 논문 찾아보기"]))
    accepted = client.Reply(content="해보기로 한 내용을 기억할게요.", question=None, suggestions=[],
                            asked_question_id=None, rapport_cards=[], basis_ids=[], recommendations=[])
    mapped = CareerMapOutput(candidates=[], next_actions=[dict(text="연구실 논문 찾아보기", selected=False),
                                                        dict(text="재학생에게 물어보기", selected=True)])
    model = Mock(side_effect=[first, proposed, second, accepted, mapped])
    monkeypatch.setattr(client, "_parse", model)
    response = messages.send_message(session_id, messages.MessageRequest(content="무엇부터 해볼까", request_id="propose"))
    assert "연구실 논문 찾아보기" in response["message"]["content"]
    assert response["state"]["exploration"]["actions"][0]["status"] == "proposed"
    messages.send_message(session_id, messages.MessageRequest(content=decision, request_id="select"))
    result = summary.summarize_session(session_id)
    restored = sessions.get_session(session_id)
    assert result["career_map"]["next_actions"] == [dict(text="연구실 논문 찾아보기", selected=True),
                                                   dict(text="재학생에게 물어보기", selected=False)]
    assert [a["status"] for a in restored["state"]["exploration"]["actions"]] == ["selected", "proposed"]
    assert restored["career_map"] == result["career_map"]
    assert model.call_count == 5  # 메시지당 분류·답변 2번, 정리 버튼 1번.


# 사용자 결정을 메모리에 반영했더라도 응답 생성이 실패하면 결정과 메시지를 함께 취소한다.
def test_failed_reply_does_not_save_exploration_decision(session_id, monkeypatch):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        exploration.record_summary(state, [dict(text="논문 읽기", selected=False)], 0)
        save_state(conn, session_id, state, next_id)
    before = sessions.get_session(session_id)
    text = "논문을 읽어볼게요"
    classified = classification(direction_relation="none", direction_label=None,
                                exploration=exploration_signals(actions=[dict(action_id=1, text=None,
                                                           status="selected", evidence=text)]))
    monkeypatch.setattr(client, "_parse", Mock(side_effect=[classified, LLMResponseError("실패")]))
    with pytest.raises(HTTPException):
        messages.send_message(session_id, messages.MessageRequest(content=text, request_id="failed-choice"))
    after = sessions.get_session(session_id)
    assert after["state"] == before["state"]
    assert after["messages"] == before["messages"]


# 진로맵 저장 뒤 상태 저장이 실패해도 지도와 제안 기록 모두 이전 상태로 남아야 한다.
def test_summary_storage_failure_rolls_back_map_and_action_records(session_id, monkeypatch):
    stub_models(monkeypatch)
    messages.send_message(session_id, messages.MessageRequest(content="대학원 탐색", request_id="prepare"))
    before = sessions.get_session(session_id)
    monkeypatch.setattr(summary, "summarize", Mock(return_value=CareerMapOutput(
        candidates=[], next_actions=[dict(text="논문 읽기", selected=False)])))
    monkeypatch.setattr(summary, "save_state", Mock(side_effect=sqlite3.OperationalError("저장 실패")))
    with pytest.raises(sqlite3.OperationalError):
        summary.summarize_session(session_id)
    after = sessions.get_session(session_id)
    assert after["state"] == before["state"]
    assert after["career_map"] == before["career_map"]


# 이전 상담의 진로맵에 있던 선택은 복원하되, 조회만으로 원본 상태 JSON을 수정하지 않는다.
def test_legacy_exploration_restores_existing_map_actions_without_writing(session_id):
    with db.get_db() as conn:
        state, next_id = load_state(conn, session_id)
        state.pop("exploration")
        save_state(conn, session_id, state, next_id)
        old_map = dict(criteria=[], candidates=[], next_actions=[dict(text="연구실 알아보기", selected=True)],
                       based_on_message_id=1, updated_at="2026-11-06T00:00:00Z")
        conn.execute("INSERT INTO career_maps (session_id, map_json, based_on_message_id) VALUES ($1, $2, 1)",
                     (session_id, json.dumps(old_map)))
    restored = sessions.get_session(session_id)
    assert restored["state"]["exploration"]["actions"][0]["status"] == "selected"
    assert restored["career_map"] == old_map
    with db.get_db() as conn:
        raw = json.loads(conn.execute("SELECT state_json FROM session_state WHERE session_id=$1", (session_id,)).fetchone()["state_json"])
        assert "exploration" not in raw


# 차단한 응답과 형식만 고친 응답 모두, 평가에 쓸 원문·복구 내역을 메시지와 같은 판정 로그에 남긴다.
@pytest.mark.parametrize("model_reply,errors,warnings,discarded", [
    (dict(content="회로 경험을 보면 설계 분야가 잘 맞아요.", question=None, basis_ids=[99]),
     ["unconfirmed_or_unknown_basis"], [], "reply"),
    (dict(content="설계 분야는 회로를 직접 다루는 일이 많아요. 더 궁금한 점이 있나요?",
          question="어떤 부분이 가장 궁금하신가요?", basis_ids=[]),
     [], ["question_in_content"], "content_question"),
])
def test_decision_log_keeps_discarded_output(session_id, monkeypatch, model_reply, errors, warnings, discarded):
    def parse(instructions, history, schema, label):
        if label == "classify":
            return classification(request_type="information")
        return client.Reply(suggestions=[], asked_question_id=None, rapport_cards=[], recommendations=[],
                            **model_reply)

    monkeypatch.setattr(client, "_parse", Mock(side_effect=parse))
    response = messages.send_message(session_id, messages.MessageRequest(
        content="설계 분야는 어때?", request_id="discarded-" + discarded))
    content = response["message"]["content"]
    assert "살펴볼게요" not in content and content.count("?") <= 1
    decision = sessions.get_session(session_id)["controller_events"][-1]["decision"]
    assert decision["validation_errors"] == errors
    assert decision["warnings"] == warnings
    assert discarded in decision["discarded"]
    if errors:
        assert "회로 경험" not in content
        assert decision["discarded"]["reply"]["content"] == model_reply["content"]
    else:
        assert "회로를 직접 다루는 일이 많아요." in content
