"""모델·DB 없이 세 통제 규칙의 판정, 상태 전이와 응답 검증을 확인한다."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling import controller as C  # noqa: E402
from counseling.state import empty_state  # noqa: E402


# 실제 분류 모델 대신, 이 테스트에서 필요한 신호만 쉽게 바꿀 수 있는 자료를 만든다.
def signals(**changes):
    fields = {
        "new_items": [], "updates": [], "rapport_answered_ids": [],
        "request_type": "reflection", "direction_relation": "none",
        "direction_label": None, "direction_choice": "none",
        "relevant_item_ids": [], "deferred_item_ids": [], "needs_reason": False,
        "follow_up": "reflect", "rapport_consent": False, "concern_provided": False,
        "wants_concern_first": False, "rapport_category": None, "concern_tags": [], "rapport_skipped": False,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


# 저장된 경험·기준·제약과 같은 구조의 항목을 만든다. 과거 턴으로 두어 새 항목과 구분한다.
def item(item_id, text, status="confirmed", turn=1):
    return {"id": item_id, "text": text, "status": status, "turn": turn, "evidence": text}


# 라포를 마친 상담을 준비해, 세 통제 규칙 자체의 판단을 라포 질문과 분리한다.
def state_with_items():
    state = empty_state()
    C.ensure_controller(state)
    state["rapport"].update(done=True, done_turn=0, end_reason="completed")
    state["conversation_stage"] = "career"
    state["experiences"] = [item(1, "회로를 직접 만들어 본 경험")]
    state["criteria"] = [item(2, "직접 만들고 개선할 수 있는 일")]
    return state


# 모델의 구조화 응답을 흉내 낸다. 자유 문장과 질문, 추천 근거를 각각 검사할 수 있다.
def model_reply(**changes):
    fields = {
        "content": "말씀하신 경험과 기준을 함께 살펴보겠습니다.",
        "suggestions": [], "question": "이 선택에서 가장 고민되는 점은 무엇인가요?", "asked_question_id": None,
        "rapport_cards": [], "basis_ids": [], "recommendations": [],
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


# 추천 후보도 별도 객체로 만들어, 근거가 빠지거나 잘못된 경우를 확인한다.
def recommendation(basis_ids):
    return SimpleNamespace(candidate="회로 설계 분야", reason="직접 제작하고 개선한 경험과 연결됩니다.",
                           basis_ids=basis_ids)


# 경험과 기준이 모두 확인되어 있을 때만 개인화된 추천을 허용한다.
def test_recommendation_gate_requires_confirmed_experience_and_criterion():
    state = state_with_items()
    assert C.plan_turn(state, signals(), 2)["allow_recommendation"] is True
    state["experiences"] = []
    plan = C.plan_turn(state, signals(request_type="recommendation"), 3)
    assert plan["allow_recommendation"] is False
    assert plan["question"] == "gather_experience"
    assert "recommendation_gate" in plan["triggered_rules"]


# 철회하거나 확신을 유보한 항목은 기존에 확인되었더라도 추천 자격에서 즉시 제외한다.
@pytest.mark.parametrize("section,status,question", [
    ("experiences", "withdrawn", "gather_experience"),
    ("experiences", "unconfirmed", "confirm"),
    ("criteria", "withdrawn", "gather_criterion"),
    ("criteria", "unconfirmed", "confirm"),
])
def test_gate_recomputes_after_withdrawal_or_uncertainty(section, status, question):
    state = state_with_items()
    state[section][0]["status"] = status
    plan = C.plan_turn(state, signals(request_type="recommendation"), 2)
    assert plan["allow_recommendation"] is False
    assert plan["question"] == question


# 일반 정보 요청에는 경험과 기준이 없어도 답할 수 있으며 정보 수집 질문을 강요하지 않는다.
def test_general_information_does_not_require_gathering_personal_data():
    state = state_with_items()
    state["experiences"] = []
    state["criteria"] = []
    plan = C.plan_turn(state, signals(request_type="information"), 2)
    assert plan["allow_recommendation"] is False
    assert plan["question"] == "reflect_after_answer"
    assert plan["fixed_question"] is None


# 현재 방향의 아홉 번째 탐색 다음 턴에서 정확히 열 번째 확인 질문을 선택한다.
def test_direction_nine_to_ten_selects_fixed_checkpoint():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=9)
    plan = C.plan_turn(state, signals(direction_relation="same"), 10)
    assert direction["turns"] == C.DIRECTION_LIMIT
    assert direction["awaiting_choice"] is True
    assert plan["question"] == "direction_check"
    assert "반도체 연구" in plan["fixed_question"]
    assert "10" in plan["fixed_question"]


# 동일 턴의 계획을 중복 계산해도 탐색 횟수를 두 번 올리지 않는다.
def test_direction_counter_is_idempotent_for_same_turn():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=8)
    C.plan_turn(state, signals(direction_relation="same"), 9)
    C.plan_turn(state, signals(direction_relation="same"), 9)
    assert direction["turns"] == 9


# 지속·전환 선택을 기다리는 동안에는 같은 분야 이야기가 더 와도 횟수를 동결한다.
def test_pending_direction_choice_freezes_counter():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=10, awaiting_choice=True)
    plan = C.plan_turn(state, signals(direction_relation="same"), 11)
    assert direction["turns"] == 10
    assert plan["question"] == "direction_check"


# 계속 탐색하겠다는 선택은 기존 방향을 유지하면서 다음 열 턴을 새로 계산하게 한다.
def test_continue_choice_resets_counter_without_losing_direction():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=10, awaiting_choice=True)
    plan = C.plan_turn(state, signals(direction_choice="continue", direction_relation="same"), 11)
    assert direction["label"] == "반도체 연구"
    assert direction["turns"] == 0
    assert direction["awaiting_choice"] is False
    assert plan["question"] != "direction_check"


# 다른 방향을 원하지만 아직 새 분야를 정하지 않았다면 선택지를 대신 정하지 않고 묻는다.
def test_switch_without_new_direction_resets_and_asks_for_direction():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=10, awaiting_choice=True)
    plan = C.plan_turn(state, signals(direction_choice="switch"), 11)
    assert direction["label"] is None
    assert direction["turns"] == 0
    assert direction["awaiting_choice"] is False
    assert plan["question"] == "clarify_direction"


# 전환 의사와 새 분야를 함께 밝히면 새 분야의 첫 탐색 턴으로 기록한다.
def test_switch_with_new_direction_starts_new_counter():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=10, awaiting_choice=True)
    C.plan_turn(state, signals(direction_choice="switch", direction_relation="new",
                               direction_label="교육 연구"), 11)
    assert direction["label"] == "교육 연구"
    assert direction["turns"] == 1
    assert direction["awaiting_choice"] is False


# 선택을 유보한 사용자에게는 같은 양자택일을 반복하지 않고 고민되는 점을 묻는다.
def test_unsure_direction_choice_preserves_checkpoint_and_offers_help():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=10, awaiting_choice=True)
    plan = C.plan_turn(state, signals(direction_choice="unsure"), 11)
    assert direction["turns"] == 10
    assert direction["awaiting_choice"] is True
    assert plan["question"] == "direction_help"


# 방향을 판별하기 어려운 턴은 누적 횟수를 유지하고 탐색 방향을 확인한다.
def test_unclear_direction_holds_counter():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=4)
    plan = C.plan_turn(state, signals(direction_relation="unclear"), 5)
    assert direction["turns"] == 4
    assert plan["question"] == "clarify_direction"


# 같은 방향으로 분류된 동의어는 저장된 대표 이름을 바꾸거나 횟수를 초기화하지 않는다.
def test_same_direction_synonym_keeps_existing_label():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=3)
    C.plan_turn(state, signals(direction_relation="same", direction_label="반도체 연구직"), 4)
    assert direction["label"] == "반도체 연구"
    assert direction["turns"] == 4


# 사용자가 먼저 새 분야로 옮겼을 때는 이전 방향의 열 번째 확인을 강요하지 않는다.
def test_new_direction_resets_previous_counter():
    state = state_with_items()
    direction = state["controller"]["direction"]
    direction.update(label="반도체 연구", turns=9)
    plan = C.plan_turn(state, signals(direction_relation="new", direction_label="교육 연구"), 10)
    assert direction["turns"] == 1
    assert plan["question"] != "direction_check"


# 과거에 남은 관련 해석도 하나씩 확인하며, 실제로 전달한 질문만 확인 완료로 기록한다.
def test_older_relevant_unconfirmed_items_are_asked_one_at_a_time():
    state = state_with_items()
    state["constraints"] = [item(3, "졸업 직후 취업", "unconfirmed"), item(4, "울산 거주", "unconfirmed")]
    result = signals(relevant_item_ids=[3, 4])
    first = C.plan_turn(state, result, 4)
    assert first["confirm_item_ids"] == [3]
    assert state["controller"]["pending_confirmation_ids"] == [3, 4]
    delivered = C.finalize_reply(state, first, model_reply())
    C.complete_turn(state, first, delivered)
    second = C.plan_turn(state, result, 5)
    assert second["confirm_item_ids"] == [4]
    assert state["controller"]["pending_confirmation_ids"] == [4]


# 사용자가 지금 판단을 유보한 항목은 관련 주제가 다시 나와도 같은 내용으로 재촉하지 않는다.
def test_deferred_confirmation_does_not_loop():
    state = state_with_items()
    state["constraints"] = [item(3, "졸업 직후 취업", "unconfirmed")]
    first = C.plan_turn(state, signals(relevant_item_ids=[3], deferred_item_ids=[3]), 4)
    second = C.plan_turn(state, signals(relevant_item_ids=[3]), 5)
    assert first["confirm_item_ids"] == []
    assert second["confirm_item_ids"] == []
    assert state["controller"]["pending_confirmation_ids"] == []


# 확인 대기 중이던 해석에 사용자가 동의하면 대기열에서 제거하고 정상 근거로 사용할 수 있다.
def test_confirmed_item_is_removed_from_pending_queue():
    state = state_with_items()
    state["constraints"] = [item(3, "졸업 직후 취업", "unconfirmed")]
    C.plan_turn(state, signals(relevant_item_ids=[3]), 4)
    state["constraints"][0]["status"] = "confirmed"
    plan = C.plan_turn(state, signals(), 5)
    assert plan["confirm_item_ids"] == []
    assert 3 in plan["allowed_basis_ids"]
    assert state["controller"]["pending_confirmation_ids"] == []


# 동기를 알 수 없는 선택은 통념으로 해석한 항목을 만들지 않고 열린 이유 질문으로 확인한다.
def test_missing_motivation_uses_open_reason_question():
    state = state_with_items()
    plan = C.plan_turn(state, signals(needs_reason=True), 4)
    assert plan["question"] == "explore_reason"
    assert plan["fixed_question"]
    assert plan["confirm_item_ids"] == []
    assert "confirm_interpretation" in plan["triggered_rules"]


# 같은 항목이라도 사용자가 내용을 정정하면 새 버전으로 보고 다시 확인할 수 있다.
def test_changed_interpretation_can_be_confirmed_again():
    state = state_with_items()
    state["constraints"] = [item(3, "졸업 직후 취업", "unconfirmed")]
    first = C.plan_turn(state, signals(relevant_item_ids=[3]), 4)
    C.complete_turn(state, first, C.finalize_reply(state, first, model_reply()))
    state["constraints"][0]["text"] = "경제적 독립"
    state["corrections"].append({
        "target_id": 3, "turn": 5,
        "before": {"text": "졸업 직후 취업", "status": "unconfirmed"},
        "after": {"text": "경제적 독립", "status": "unconfirmed"},
        "evidence": "꼭 바로 취업할 필요는 없어요",
    })
    second = C.plan_turn(state, signals(), 5)
    assert second["confirm_item_ids"] == [3]
    assert second["confirm_items"] == ["경제적 독립"]


# 라포 종료와 해석 확인이 겹쳐도 질문은 하나만 하고, 보류한 카드 제시는 다음 턴에 이어간다.
def test_confirmation_postpones_card_offer_until_next_turn():
    state = state_with_items()
    state["conversation_stage"] = "rapport"
    state["rapport"].update(done_turn=6)
    state["constraints"] = [item(3, "졸업 직후 취업", "unconfirmed")]
    first = C.plan_turn(state, signals(relevant_item_ids=[3]), 6)
    assert first["question"] == "confirm"
    assert first["rapport_action"]["type"] == "none"
    assert state["controller"]["pending_card_offer"] == "wrap_up"
    C.complete_turn(state, first, C.finalize_reply(state, first, model_reply()))
    second = C.plan_turn(state, signals(), 7)
    assert second["rapport_action"]["type"] == "wrap_up"
    assert second["question"] == "none"
    assert state["conversation_stage"] == "cards"
    C.complete_turn(state, second, C.finalize_reply(state, second, model_reply()))
    assert state["controller"]["pending_card_offer"] is None


# 일반 정보 설명 중 갑자기 개인화된 추천을 내놓는 경우도 요청 유형에 따라 차단한다.
def test_recommendation_is_rejected_when_user_only_requested_information():
    state = state_with_items()
    plan = C.plan_turn(state, signals(request_type="information"), 2)
    reply = C.finalize_reply(state, plan, model_reply(recommendations=[recommendation([1, 2])]))
    assert "recommendation_not_allowed" in reply.validation_errors
    assert "회로 설계 분야" not in reply.content


# 추천 근거가 전혀 없는 상담에서는 모델이 후보를 반환해도 사용자에게 전달하지 않는다.
def test_recommendation_is_rejected_when_gate_is_closed():
    state = state_with_items()
    state["experiences"] = []
    plan = C.plan_turn(state, signals(request_type="recommendation"), 2)
    reply = C.finalize_reply(state, plan, model_reply(recommendations=[recommendation([2])]))
    assert "recommendation_not_allowed" in reply.validation_errors
    assert "회로 설계 분야" not in reply.content
    assert reply.question == plan["fixed_question"]


# 후보별 근거에는 확인된 경험과 기준이 모두 있어야 하며, 없는 항목이나 미확인 항목도 금지한다.
@pytest.mark.parametrize("basis_ids", [[1], [2], [], [1, 2, 99], [1, 2, 3]])
def test_recommendation_requires_known_confirmed_experience_and_criterion_basis(basis_ids):
    state = state_with_items()
    state["constraints"] = [item(3, "졸업 직후 취업", "unconfirmed")]
    plan = C.plan_turn(state, signals(request_type="recommendation"), 2)
    reply = C.finalize_reply(state, plan, model_reply(recommendations=[recommendation(basis_ids)]))
    assert "recommendation_missing_confirmed_basis" in reply.validation_errors
    assert "회로 설계 분야" not in reply.content


# 일반 설명의 개인화된 근거도 확인된 항목만 사용할 수 있다.
@pytest.mark.parametrize("basis_ids", [[3], [99]])
def test_unknown_or_unconfirmed_general_basis_is_rejected(basis_ids):
    state = state_with_items()
    state["constraints"] = [item(3, "졸업 직후 취업", "unconfirmed")]
    plan = C.plan_turn(state, signals(request_type="information"), 2)
    reply = C.finalize_reply(state, plan, model_reply(basis_ids=basis_ids))
    assert "unconfirmed_or_unknown_basis" in reply.validation_errors
    assert reply.used_basis_ids == []


# 추천 자격과 요청, 근거가 모두 적절한 후보는 최종 답변에 포함하고 근거를 기록한다.
def test_valid_requested_recommendation_is_delivered_with_basis():
    state = state_with_items()
    plan = C.plan_turn(state, signals(request_type="recommendation"), 2)
    reply = C.finalize_reply(state, plan, model_reply(recommendations=[recommendation([1, 2])]))
    assert reply.validation_errors == []
    assert "회로 설계 분야" in reply.content
    assert reply.used_basis_ids == [1, 2]


# 고정 질문은 모델 질문을 덮어쓰며, 다른 라포 질문을 물었다는 보고도 받아들이지 않는다.
def test_fixed_question_overrides_model_question_and_drops_rapport_id():
    state = state_with_items()
    state["controller"]["direction"].update(label="반도체 연구", turns=9)
    plan = C.plan_turn(state, signals(direction_relation="same"), 10)
    reply = C.finalize_reply(state, plan, model_reply(question="다른 질문을 해도 될까요?", asked_question_id=17))
    assert reply.question == plan["fixed_question"]
    assert reply.content.count(plan["fixed_question"]) == 1
    assert "다른 질문" not in reply.content
    assert reply.asked_question_id is None


# 본문에 질문을 섞어 한 턴에 여러 질문이 나갈 위험이 있으면 안전한 응답으로 대체한다.
def test_question_in_content_is_replaced_by_safe_response():
    state = state_with_items()
    plan = C.plan_turn(state, signals(request_type="information"), 2)
    reply = C.finalize_reply(state, plan, model_reply(content="다른 직업도 궁금하신가요?"))
    assert "question_in_content" in reply.validation_errors
    assert "다른 직업도" not in reply.content


# 비교 실험에서 통제를 끄면 조건 판정과 방향 카운터 강제를 적용하지 않는다.
def test_disabled_controller_bypasses_gates_and_basis_validation():
    state = state_with_items()
    state["experiences"] = []
    state["controller"]["enabled"] = False
    state["controller"]["direction"].update(label="반도체 연구", turns=9)
    plan = C.plan_turn(state, signals(request_type="recommendation", direction_relation="same"), 10)
    assert plan["allow_recommendation"] is True
    assert state["controller"]["direction"]["turns"] == 9
    assert plan["triggered_rules"] == []
    reply = C.finalize_reply(state, plan, model_reply(basis_ids=[99], recommendations=[recommendation([99])]))
    assert reply.validation_errors == []
    assert "회로 설계 분야" in reply.content


# 카드 선택 직후에는 현재 진로 고민을 묻고, 기존 기준과 추천 근거 제한·탐색 횟수는 유지한다.
def test_card_plan_applies_gate_without_incrementing_direction_counter():
    state = state_with_items()
    state["controller"]["direction"].update(label="반도체 연구", turns=4)
    card = {"name": "뚝딱 메이커", "criterion": "직접 만들고 개선하는 일", "meaning": "직접 만들어 보고 싶어요"}
    plan = C.plan_turn(state, None, 8, card=card)
    assert plan["question"] == "career_concern"
    assert plan["fixed_question"] == "지금 진로와 관련해서 어떤 고민이 있으신가요?"
    assert plan["allow_recommendation"] is True
    assert plan["allowed_basis_ids"] == [1, 2]
    assert plan["experience_ids"] == [1]
    assert plan["criterion_ids"] == [2]
    assert state["criteria"] == [item(2, "직접 만들고 개선할 수 있는 일")]
    assert state["controller"]["direction"]["turns"] == 4
    assert state["controller"]["last_processed_turn"] is None


# 카드로 상담을 시작하는 질문은 이전 확인 대기·방향 확인보다 먼저 나오며, 통제를 꺼도 같다.
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("previous_state", ["confirmation", "clarification", "checkpoint"])
def test_card_concern_question_precedes_old_pending_questions(enabled, previous_state):
    state = state_with_items()
    state["controller"]["enabled"] = enabled
    state["constraints"] = [item(3, "졸업 직후 취업", "unconfirmed")]
    state["controller"]["direction"].update(label="반도체 연구", turns=9)
    if previous_state == "confirmation":
        state["controller"]["pending_confirmation_ids"] = [3]
    elif previous_state == "clarification":
        state["controller"]["direction"]["needs_clarification"] = True
    else:
        state["controller"]["direction"].update(turns=10, awaiting_choice=True)
    direction_before = dict(state["controller"]["direction"])
    pending_before = list(state["controller"]["pending_confirmation_ids"])
    card = {"name": "뚝딱 메이커", "criterion": "직접 만들고 개선하는 일"}
    plan = C.plan_turn(state, None, 12, card=card)
    reply = C.finalize_reply(state, plan, model_reply(question="그 기준이 중요했던 경험은 무엇인가요?"))
    C.complete_turn(state, plan, reply)
    assert plan["question"] == "career_concern"
    assert reply.question == "지금 진로와 관련해서 어떤 고민이 있으신가요?"
    assert reply.content.endswith(reply.question)
    assert "그 기준이 중요했던 경험" not in reply.content
    assert state["controller"]["direction"] == direction_before
    assert state["controller"]["pending_confirmation_ids"] == pending_before
    assert state["controller"]["last_question"]["type"] == "career_concern"
    assert state["criteria"] == [item(2, "직접 만들고 개선할 수 있는 일")]
    assert plan["allowed_basis_ids"] == [1, 2]


# 현재 고민을 묻는 카드 전환도 확인된 경험이 없으면 개인화된 추천을 허용하지 않는다.
def test_card_concern_question_retains_recommendation_gate():
    state = state_with_items()
    state["experiences"] = []
    card = {"name": "뚝딱 메이커", "criterion": "직접 만들고 개선하는 일"}
    plan = C.plan_turn(state, None, 3, card=card)
    assert plan["question"] == "career_concern"
    assert plan["allow_recommendation"] is False
    assert plan["allowed_basis_ids"] == [2]
    assert plan["experience_ids"] == []
    assert "recommendation_gate" in plan["triggered_rules"]


# 이전 상담의 일부 필드만 저장되어 있어도 새 기본값을 채우고 기존 설정은 유지한다.
def test_ensure_controller_preserves_existing_settings_and_adds_missing_fields():
    state = state_with_items()
    state["controller"] = {"enabled": False, "direction": {"label": "교육 연구", "turns": 3}}
    controller = C.ensure_controller(state)
    assert controller["enabled"] is False
    assert controller["direction"]["label"] == "교육 연구"
    assert controller["direction"]["turns"] == 3
    assert controller["direction"]["awaiting_choice"] is False
    assert controller["pending_confirmation_ids"] == []
