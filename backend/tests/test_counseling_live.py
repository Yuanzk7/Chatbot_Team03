"""프롬프트 의미를 실제 설정 모델로 검사한다. CAREERMATE_LIVE_TESTS=1일 때만 API를 호출한다."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling import exploration as E  # noqa: E402
from counseling.controller import complete_turn, plan_turn  # noqa: E402
from counseling.state import empty_state, merge  # noqa: E402
from llm import client  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.getenv("CAREERMATE_LIVE_TESTS") != "1",
    reason="실제 모델 검증은 CAREERMATE_LIVE_TESTS=1로 명시적으로 실행할 때만 API를 호출합니다.",
)

CONCERN = "대학원을 만약 2년동안 석사를 하는건데 과연 이 2년동안 내가 배워갈 수 있는게 있는 지를 모르겠어"

TOPIC_HISTORY = [
    {"role": "user", "content": CONCERN},
    {"role": "assistant", "content": "대학원 연구가 실제로 어떤 과정인지 확인해 볼 수 있어요. 대학원에서 탐구하는 과정 중 어떤 점을 더 알고 싶으신가요?"},
    {"role": "user", "content": "연구 주제를 정하는 과정"},
    {"role": "assistant", "content": "관심 있는 현상에서 출발해 관련 논문을 읽으며 검증할 질문을 좁혀 갑니다. 주제를 정하는 과정에서 어떤 부분이 가장 궁금하신가요?"},
    {"role": "user", "content": "논문에서 주제를 찾는 법"},
    {"role": "assistant", "content": "논문의 연구 질문, 가정, 한계와 설명되지 않은 사례를 살펴볼 수 있어요. 논문을 읽는다면 어떤 점을 먼저 확인해보고 싶으신가요?"},
]


# 저장 없이 진학·취업을 고민하는 상태를 만든다. 기존 기준을 유지하는 사례도 같은 조건으로 비교한다.
def career_state(with_criterion):
    state = empty_state()
    state.update(preferred_name="유찬", conversation_stage="career", career_concern_received=True)
    state["rapport"].update(done=True, done_turn=0, end_reason="completed")
    state["controller"]["direction"].update(label="대학원 진학과 취업", turns=1)
    if with_criterion:
        state["criteria"] = [dict(id=1, text="성장", status="confirmed", evidence="성장이 중요해", turn=1)]
    return state


# 실제 호출 결과를 보관해 실패 시 검증 전 구조화 출력도 확인한다. API 호출 횟수는 늘리지 않는다.
@pytest.fixture
def model_outputs(monkeypatch):
    parsed = []
    original_parse = client._parse

    # 분류·응답을 그대로 통과시키면서 검증에 필요한 원본만 기록한다.
    def capture_parse(*args):
        value = original_parse(*args)
        parsed.append(value)
        return value

    monkeypatch.setattr(client, "_parse", capture_parse)
    return parsed


# 결과를 모르는 고민과 기준 자체의 유보를 구별하고, 문제 제보 사례의 최종 질문까지 실제로 확인한다.
@pytest.mark.parametrize("message,with_criterion,expected_status", [
    (CONCERN, False, None),
    ("성장하는 게 중요하지만 대학원에서 성장할 수 있을지는 모르겠어", False, "confirmed"),
    ("성장이 중요한 건 맞아. 하지만 대학원에서 성장할 수 있을지는 모르겠어", True, "confirmed"),
    ("성장이 나한테 정말 중요한 기준인지는 아직 잘 모르겠어", True, "unconfirmed"),
])
def test_outcome_uncertainty_is_distinct_from_uncertain_criterion(message, with_criterion, expected_status):
    state = career_state(with_criterion)
    result = client.classify(state, [], message)
    merged, _ = merge(state, result, turn=2, next_id=2 if with_criterion else 1)
    criteria = merged["criteria"]
    if expected_status is not None:
        assert len(criteria) == 1, result.model_dump()
        assert criteria[0]["status"] == expected_status, result.model_dump()
        return
    assert result.new_items == [], result.model_dump()
    assert result.updates == [], result.model_dump()
    assert result.needs_reason is False
    plan = plan_turn(merged, result, turn=2)
    assert plan["question"] == "optional", plan
    reply = client.generate_reply(merged, [], message, turn=2, plan=plan)
    assert reply.validation_errors == []
    assert reply.content and reply.content.count("?") <= 1
    assert "어떻게 생각하시나요" not in (reply.question or "")
    assert "중요한 고민으로 짚어" not in reply.content
    print("\n제보 사례 실제 응답:\n" + reply.content)


# 짧은 주제 선택·설명 동의도 정보 요청으로 받아들이고, 추가 요청 없이 같은 턴에 설명한다.
@pytest.mark.parametrize("message,history", [
    ("연구 주제를 정하는 과정을 설명해줘", TOPIC_HISTORY[:2]),
    ("데이터와 분석 방법", TOPIC_HISTORY),
    ("논문에서 주제를 찾는 법", TOPIC_HISTORY[:4]),
    ("네, 좋아요", [
        {"role": "user", "content": CONCERN},
        {"role": "assistant", "content": "석사 과정에서 배우는 내용을 논문 읽기와 연구 방법을 중심으로 설명해 드릴까요?"},
    ]),
])
def test_information_explanations_do_not_restart_topic_selection(message, history, model_outputs):
    state = career_state(False)
    result = client.classify(state, history, message)
    assert result.request_type == "information", result.model_dump()
    assert result.needs_reason is False
    merged, _ = merge(state, result, turn=4, next_id=1)
    plan = plan_turn(merged, result, turn=4)
    assert plan["question"] == "optional", plan
    reply = client.generate_reply(merged, history, message, turn=4, plan=plan)
    assert reply.validation_errors == [], model_outputs[-1].model_dump()
    assert reply.question is None, reply.content
    assert reply.suggestions == []
    assert reply.content
    if message == "데이터와 분석 방법":
        assert "데이터" in reply.content and "분석" in reply.content
    elif message in ("논문에서 주제를 찾는 법", "네, 좋아요"):
        assert result.new_items == [] and result.updates == [], result.model_dump()
        assert result.exploration is None or not result.exploration.actions, result.model_dump()
        assert "논문" in reply.content
        assert any(word in reply.content for word in ("질문", "한계", "분석", "검증", "방법"))
    print("\n정보 설명 실제 응답:\n" + reply.content)


# 짧아진 프롬프트에서도 시작 동의·지정 라포 질문·후보 숨김이 실제 모델로 연결되는지 검사한다.
def test_rapport_consent_keeps_the_assigned_question_without_suggestions(model_outputs):
    state = empty_state()
    state["preferred_name"] = "유찬"
    message = "네, 여쭤보세요"
    history = [{"role": "assistant", "content": "몇 가지 여쭤봐도 될까요?"}]
    result = client.classify(state, history, message)
    assert result.rapport_consent is True
    merged, _ = merge(state, result, turn=1, next_id=1)
    plan = plan_turn(merged, result, turn=1)
    assert merged["conversation_stage"] == "rapport"
    reply = client.generate_reply(merged, history, message, turn=1, plan=plan)
    assert reply.validation_errors == [], model_outputs[-1].model_dump()
    assert reply.question and reply.content.count("?") == 1
    assert reply.asked_question_id == plan["rapport_action"]["question"]["id"]
    assert reply.suggestions == []


# 실제 비교를 전달한 뒤에만 다음 행동으로 이동하며, 별도 단계 판정 API를 호출하지 않는다.
def test_exploration_comparison_advances_after_actual_delivery(model_outputs):
    state = career_state(True)
    state["experiences"] = [dict(id=2, text="모델 구현", status="confirmed", evidence="구현해봤어", turn=1)]
    state["exploration"]["candidates"] = [dict(name=name, source="user", active=True) for name in ("대학원", "취업")]
    history = [{"role": "user", "content": "모델을 구현해 본 적이 있고, 성장하는 게 중요해요. 대학원과 취업을 고민하고 있어요."}]
    message = "현실 조건은 특별히 없어요. 대학원과 취업을 비교해 보고 싶어요. 선택을 대신 해줄 필요는 없어요."
    result = client.classify(state, history, message)
    merged, _ = merge(state, result, 2, 3, user_message=message)
    plan = plan_turn(merged, result, 2)
    assert plan["exploration"] and plan["exploration"]["focus"] == "comparison", result.model_dump()
    assert merged["exploration"]["comparison"] is None
    reply = client.generate_reply(merged, history, message, 2, plan)
    assert reply.validation_errors == [], model_outputs[-1].model_dump()
    complete_turn(merged, plan, reply)
    assert merged["exploration"]["comparison"] is not None, model_outputs[-1].model_dump()
    next_plan = E.plan_turn(merged, dict(question="optional", follow_up="reflect", request_type="reflection"))
    assert next_plan["focus"] == "action"
    assert len(model_outputs) == 2


# 단순 호응은 여러 제안 중 하나를 선택한 것으로 만들지 않는다.
def test_exploration_ambiguous_agreement_keeps_actions_proposed():
    state = career_state(True)
    state["exploration"]["focus"] = "action"
    E.record_summary(state, [dict(text="논문 읽기", selected=False), dict(text="재학생에게 물어보기", selected=False)], 1)
    state["exploration"]["last_proposed_ids"] = [1, 2]
    state["controller"]["last_question"] = dict(type="optional", item_ids=[], text="어떤 일을 해보고 싶으신가요?")
    history = [{"role": "assistant", "content": "논문을 읽거나 재학생에게 물어볼 수 있어요. 어떤 일을 해보고 싶으신가요?"}]
    result = client.classify(state, history, "좋아요")
    merged, _ = merge(state, result, 2, 2, user_message="좋아요")
    assert all(a["status"] == "proposed" for a in merged["exploration"]["actions"]), result.model_dump()


# 명시적인 실행 약속과 철회는 실제 사용자 인용과 행동 ID로 연결한다.
@pytest.mark.parametrize("message,status", [
    ("이번 주에 연구실 논문을 찾아볼게요", "selected"),
    ("연구실 논문을 찾아보는 건 하지 않을래요", "withdrawn"),
])
def test_exploration_explicit_action_decision(message, status):
    state = career_state(True)
    E.record_summary(state, [dict(text="연구실 논문 찾아보기", selected=False)], 1)
    history = [{"role": "assistant", "content": "연구실 논문을 찾아보며 실제 연구 내용을 살펴볼 수 있어요."}]
    result = client.classify(state, history, message)
    merged, _ = merge(state, result, 2, 2, user_message=message)
    assert merged["exploration"]["actions"][0]["status"] == status, result.model_dump()


# 특정 분야·직무에 대한 관심은 선택 기준으로 기록하지 않고, 중요하게 여기는 조건만 기준으로 기록한다.
@pytest.mark.parametrize("message,expects_criterion", [
    ("인공지능 쪽에 관심이 있는데 나한테 맞는 분야 하나만 선택해줘", False),
    ("반도체 공정 엔지니어 직무에 관심이 있어요", False),
    ("저는 계속 배우면서 성장할 수 있는 환경이 중요해요", True),
])
def test_field_interest_is_not_recorded_as_criterion(message, expects_criterion, model_outputs):
    result = client.classify(career_state(with_criterion=False), [], message)
    kinds = [item.kind for item in result.new_items]
    assert ("criterion" in kinds) is expects_criterion, model_outputs


# 특정 학교·기관의 현재 정보를 묻는 질문만 최신 정보가 필요하다고 분류해 웹 검색 대상이 되게 한다.
@pytest.mark.parametrize("message,expected", [
    ("우리학교(UNIST)의 멀티모달 주제를 맡는 연구실을 알려줘", True),
    ("KAIST 대학원 원서 마감이 언제야?", True),
    ("멀티모달 AI가 뭐야?", False),
    ("대학원과 취업 중에 뭐가 나을지 고민이에요", False),
])
def test_current_info_signal_marks_only_time_sensitive_questions(message, expected, model_outputs):
    result = client.classify(career_state(with_criterion=False), [], message)
    assert result.needs_current_info is expected, model_outputs
