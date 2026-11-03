"""프롬프트 의미를 실제 설정 모델로 검사한다. CAREERMATE_LIVE_TESTS=1일 때만 API를 호출한다."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling.controller import plan_turn  # noqa: E402
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


# 정보 질문을 계속 잘게 나누지 않고, 특히 함께 요청한 데이터·분석 방법을 한 번에 설명하는지 검사한다.
@pytest.mark.parametrize("message,history", [
    ("연구 주제를 정하는 과정을 설명해줘", TOPIC_HISTORY[:2]),
    ("데이터와 분석 방법", TOPIC_HISTORY),
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
