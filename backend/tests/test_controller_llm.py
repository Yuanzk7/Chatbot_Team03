"""실제 API 대신 구조화 응답을 주입해, 모델 입출력과 컨트롤러 사이의 계약을 검증한다."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai.lib._pydantic import to_strict_json_schema  # noqa: E402

from counseling.controller import plan_turn  # noqa: E402
from counseling.rapport import apply_reply  # noqa: E402
from counseling.state import Classification, empty_state, format_state  # noqa: E402
from llm import client  # noqa: E402


# 진로 상담 단계에서 응답 계약을 검사할 기본 상태를 만든다.
def career_state():
    state = empty_state()
    state["conversation_stage"] = "career"
    state["rapport"].update(done=True, done_turn=0, end_reason="completed")
    return state


# 분류기의 실제 스키마로 입력 신호를 만들어 API 출력과 동일한 자료형을 사용한다.
def classification(**changes):
    fields = dict(preferred_name=None, new_items=[], updates=[], rapport_category=None,
                  concern_tags=[], rapport_answered_ids=[], rapport_skipped=False,
                  wants_concern_first=False, request_type="reflection", direction_relation="none",
                  direction_label=None, direction_choice="none", needs_reason=False,
                  follow_up="reflect", rapport_consent=False, concern_provided=False)
    fields.update(changes)
    return Classification(**fields)


# 응답기의 실제 스키마로 질문·근거·추천을 분리한 구조화 응답을 만든다.
def reply(**changes):
    fields = dict(content="함께 살펴보겠습니다.", question=None, suggestions=[],
                  asked_question_id=None, rapport_cards=[], basis_ids=[], recommendations=[])
    fields.update(changes)
    return client.Reply(**fields)


# strict schema의 모든 객체가 추가 키를 막고 모든 속성을 요구하는지 재귀적으로 검사한다.
def assert_strict_schema(node):
    if isinstance(node, dict):
        assert "default" not in node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        for value in node.values():
            assert_strict_schema(value)
    elif isinstance(node, list):
        for value in node:
            assert_strict_schema(value)


# 분류·응답 스키마가 설치된 SDK의 strict 구조화 출력 형식으로 변환되는지 확인한다.
@pytest.mark.parametrize("schema", [Classification, client.Reply])
def test_llm_schemas_are_strict_without_unsupported_defaults(schema):
    assert_strict_schema(to_strict_json_schema(schema))


# 고정 질문은 모델의 다른 질문을 대체하고, 기존 대화 뒤에 현재 입력을 정확히 한 번 전달한다.
def test_generate_reply_connects_plan_schema_and_fixed_question(monkeypatch):
    state = career_state()
    state["controller"]["direction"].update(label="반도체 연구", turns=9)
    plan = plan_turn(state, classification(direction_relation="same"), 10)
    model = Mock(return_value=reply(question="모델이 임의로 만든 질문인가요?"))
    monkeypatch.setattr(client, "_parse", model)
    history = [{"role": "assistant", "content": "어떤 실험을 했나요?"}]
    result = client.generate_reply(state, history, "회로를 만들었어요", 10, plan)
    instructions, messages, schema, label = model.call_args.args
    assert schema is client.Reply
    assert label == "reply"
    assert plan["fixed_question"] in instructions
    assert messages == history + [{"role": "user", "content": "회로를 만들었어요"}]
    assert result.question == plan["fixed_question"]
    assert "모델이 임의로" not in result.content
    assert model.call_count == 1


# 경험·기준이 없어도 일반 사실 설명은 통과하며 개인화된 질문을 억지로 덧붙이지 않는다.
def test_generate_information_reply_without_personal_items(monkeypatch):
    state = career_state()
    plan = plan_turn(state, classification(request_type="information", follow_up="answer_only"), 1)
    content = "공정 엔지니어는 제조 공정의 조건을 관리하고 개선하는 일을 해요."
    monkeypatch.setattr(client, "_parse", Mock(return_value=reply(content=content)))
    result = client.generate_reply(state, [], "공정 엔지니어는 무슨 일을 해요?", 1, plan)
    assert result.content == content
    assert result.question is None
    assert result.validation_errors == []


# 질문 생략을 별도로 요청하지 않아도 일반 상담의 유효한 설명을 그대로 전달해야 한다.
@pytest.mark.parametrize("request_type", ["information", "reflection"])
def test_normal_reply_can_finish_without_follow_up_question(monkeypatch, request_type):
    state = career_state()
    plan = plan_turn(state, classification(request_type=request_type), 1)
    content = "말씀하신 두 부분을 함께 살펴볼 수 있어요."
    model = Mock(return_value=reply(content=content, suggestions=["데이터부터", "분석부터"]))
    monkeypatch.setattr(client, "_parse", model)
    result = client.generate_reply(state, [], "데이터와 분석 방법", 1, plan)
    assert result.content == content
    assert result.question is None
    assert result.suggestions == []
    assert result.validation_errors == []
    assert model.call_count == 1


# 모델이 금지된 추천을 반환해도 재호출 없이 질문으로 바뀌고 검증 실패 이유가 남는다.
def test_generate_reply_rejects_forbidden_recommendation_without_extra_call(monkeypatch):
    state = career_state()
    plan = plan_turn(state, classification(request_type="recommendation"), 1)
    model = Mock(return_value=reply(recommendations=[
        client.Recommendation(candidate="반도체 연구직", reason="잘 맞을 것 같아요.", basis_ids=[]),
    ]))
    monkeypatch.setattr(client, "_parse", model)
    result = client.generate_reply(state, [], "추천해줘", 1, plan)
    assert "반도체 연구직" not in result.content
    assert result.question == plan["fixed_question"]
    assert "recommendation_not_allowed" in result.validation_errors
    assert model.call_count == 1


# API가 미완료 응답을 주면 검증기로 넘기기 전에 오류로 처리해 경로의 저장 작업을 막는다.
def test_incomplete_model_response_is_not_treated_as_valid_reply(monkeypatch):
    api = SimpleNamespace(responses=SimpleNamespace(parse=Mock(return_value=
                          SimpleNamespace(status="incomplete", output_parsed=None))))
    monkeypatch.setattr(client, "_client", lambda: api)
    with pytest.raises(client.LLMResponseError, match="incomplete"):
        client._parse("테스트 지시", [], client.Reply, "reply")


# 모델이 필수 질문을 빠뜨려도 코드가 원래 라포 질문을 전달하고 실제 질문 횟수를 기록한다.
def test_missing_question_fallback_records_delivered_rapport_question(monkeypatch):
    state = career_state()
    state["conversation_stage"] = "rapport"
    state["rapport"].update(done=False, done_turn=None)
    state["rapport"]["questions"] = [
        {"id": 17, "text": "지금까지 가장 재미있었던 경험은 무엇인가요?",
         "status": "pending", "asks": 0, "holds": 0},
    ]
    plan = plan_turn(state, classification(), 1)
    monkeypatch.setattr(client, "_parse", Mock(return_value=reply()))
    result = client.generate_reply(state, [], "시작해볼게요", 1, plan)
    apply_reply(state["rapport"], plan["rapport_action"], result.asked_question_id)
    assert "missing_question" in result.validation_errors
    assert result.asked_question_id == 17
    assert state["rapport"]["questions"][0]["asks"] == 1
    assert result.question == state["rapport"]["questions"][0]["text"]


# 모델이 지정 라포 질문 하나를 본문에도 똑같이 복사했으면 오류 응답으로 바꾸지 않고 한 번만 전달한다.
def test_identical_rapport_question_in_both_fields_is_delivered_once(monkeypatch):
    state = career_state()
    state["conversation_stage"] = "rapport"
    state["rapport"].update(done=False, done_turn=None)
    question = "지금까지 가장 재미있었던 경험은 무엇인가요?"
    state["rapport"]["questions"] = [dict(id=17, text=question, status="pending", asks=0, holds=0)]
    plan = plan_turn(state, classification(), 1)
    monkeypatch.setattr(client, "_parse", Mock(return_value=reply(
        content=question, question=question, asked_question_id=17)))
    result = client.generate_reply(state, [], "네", 1, plan)
    apply_reply(state["rapport"], plan["rapport_action"], result.asked_question_id)
    assert result.content == question
    assert result.question == question
    assert result.validation_errors == []
    assert state["rapport"]["questions"][0]["asks"] == 1


# 추천 설명에 다른 질문을 숨겨 본문·질문 분리를 우회하는 응답도 차단한다.
def test_recommendation_reason_cannot_add_another_question(monkeypatch):
    state = career_state()
    for section, item_id, text in [("experiences", 1, "회로 제작"), ("criteria", 2, "직접 개선하는 일")]:
        state[section] = [dict(id=item_id, text=text, status="confirmed", evidence=text, turn=1)]
    plan = plan_turn(state, classification(request_type="recommendation"), 2)
    monkeypatch.setattr(client, "_parse", Mock(return_value=reply(recommendations=[
        client.Recommendation(candidate="회로 설계", reason="이 분야는 어떤가요?", basis_ids=[1, 2]),
    ])))
    result = client.generate_reply(state, [], "추천해줘", 2, plan)
    assert "question_in_recommendation" in result.validation_errors
    assert "이 분야는 어떤가요" not in result.content


# 중간 ID가 비어 있어도 모델이 실제 항목과 근거 ID를 연결할 수 있도록 둘 다 프롬프트에 전달한다.
def test_generation_prompt_includes_actual_item_ids_and_meanings(monkeypatch):
    state = career_state()
    state["experiences"] = [dict(id=7, text="회로 제작", status="confirmed", evidence="만들었어요", turn=1)]
    state["criteria"] = [dict(id=15, text="직접 개선하는 일", status="confirmed", evidence="중요해요", turn=2)]
    plan = plan_turn(state, classification(request_type="recommendation"), 3)
    model = Mock(return_value=reply(question="이 분야를 살펴볼 때 어떤 점이 가장 궁금하신가요?", recommendations=[
        client.Recommendation(candidate="회로 설계", reason="제작 경험과 개선 기준을 함께 살펴볼 수 있어요.",
                              basis_ids=[7, 15]),
    ]))
    monkeypatch.setattr(client, "_parse", model)
    result = client.generate_reply(state, [], "추천해줘", 3, plan)
    instructions = model.call_args.args[0]
    assert "id 7: 회로 제작" in instructions
    assert "id 15: 직접 개선하는 일" in instructions
    assert result.used_basis_ids == [7, 15]
    assert result.validation_errors == []


# 카드를 고른 뒤 기준을 정정·유보·철회하면 최초 카드 의미보다 현재 항목을 우선한다.
@pytest.mark.parametrize("status", ["confirmed", "unconfirmed", "withdrawn"])
def test_chosen_card_prompt_uses_current_criterion_status_and_text(status):
    state = career_state()
    state["criteria"] = [dict(id=9, text="수정한 기준", status=status, evidence="정정했어요", turn=2, source="conversation")]
    state["rapport"]["cards"] = {
        "chosen_id": 1, "criterion_item_id": 9,
        "offered": [{"id": 1, "name": "카드 이름", "meaning": "과거 해석", "criterion": "최초 기준"}],
    }
    prompt = format_state(state)
    assert "[라포에서 선택한 카드 — 배경정보]" in prompt
    assert "[중심 기준 카드]" not in prompt
    assert "카드 이름: 과거 해석" in prompt
    if status == "withdrawn":
        assert "[확인된 선택 기준] 없음" in prompt
        assert "[미확인 선택 기준] 없음" in prompt
        assert "[철회한 정보 (withdrawn)] id 9: 수정한 기준" in prompt
    else:
        assert "id 9: 수정한 기준" in prompt
