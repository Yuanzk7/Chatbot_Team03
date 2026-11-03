"""시작 동의, 자유응답 라포, 상담 전환과 카드 배경정보의 경계를 모델 없이 검증한다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling import controller as C  # noqa: E402
from counseling.cards import DECK_BY_ID, new_cards  # noqa: E402
from counseling.career_map import active_criteria  # noqa: E402
from counseling.flow import (  # noqa: E402
    INTRO_SUGGESTIONS, apply_input, ensure_flow, history_for_model, suggestions_for_stage, without_card_criteria,
)
from counseling.state import Classification, empty_state, format_state, merge  # noqa: E402
from llm.client import Reply  # noqa: E402


# 실제 분류 스키마를 사용해 요청 유형과 후속 질문 의도를 서로 독립적으로 지정한다.
def classified(**changes):
    fields = dict(preferred_name=None, new_items=[], updates=[], rapport_category=None, concern_tags=[],
                  rapport_answered_ids=[], rapport_skipped=False, wants_concern_first=False,
                  request_type="reflection", direction_relation="none", direction_label=None,
                  direction_choice="none", needs_reason=False, follow_up="reflect",
                  rapport_consent=False, concern_provided=False)
    fields.update(changes)
    return Classification(**fields)


# 모델이 세 개의 후보를 반환해도 실제 단계에 따라 제거·교체되는지 확인할 응답을 만든다.
def generated(question="어떤 점이 가장 고민되나요?", **changes):
    fields = dict(content="말씀하신 내용을 살펴볼게요.", question=question,
                  suggestions=["첫 번째 답", "두 번째 답", "잘 모르겠어요"], asked_question_id=None,
                  rapport_cards=[], basis_ids=[], recommendations=[])
    fields.update(changes)
    return Reply(**fields)


# 라포가 끝난 진로 상담 상태를 만들어 일반 비교·종료·설명만 요청의 동작을 검증한다.
def career_state():
    state = empty_state()
    state["conversation_stage"] = "career"
    state["rapport"].update(done=True, done_turn=0, end_reason="completed")
    return state


# 초기 동의 전에는 알아가기 질문을 먼저 꺼내지 않고 두 후보만 안내한다.
def test_intro_waits_for_consent_and_has_exactly_two_suggestions():
    state = empty_state()
    plan = C.plan_turn(state, classified(), 1)
    reply = C.finalize_reply(state, plan, generated())
    assert state["conversation_stage"] == "intro"
    assert state["rapport"]["questions"] == []
    assert reply.suggestions == ["네, 여쭤보세요", "바로 고민부터 말할게요"]


# 동의하면 라포를 시작하지만, 그때부터 모델이 만든 후보 답변은 저장·표시하지 않는다.
def test_consent_starts_free_text_rapport_without_suggestions():
    state = empty_state()
    plan = C.plan_turn(state, classified(rapport_consent=True), 1)
    reply = C.finalize_reply(state, plan, generated())
    assert state["conversation_stage"] == "rapport"
    assert plan["rapport_action"]["type"] == "ask"
    assert state["controller"]["direction"]["turns"] == 0
    assert reply.suggestions == []


# '바로 고민부터'는 카드 선택까지 강제하지 않고, 실제 고민이 없을 때만 주제를 묻는다.
@pytest.mark.parametrize("provided", [True, False])
def test_direct_counseling_skips_questions_and_cards(provided):
    state = empty_state()
    plan = C.plan_turn(state, classified(wants_concern_first=True, concern_provided=provided,
                                       request_type="information"), 1)
    assert state["conversation_stage"] == "career"
    assert state["rapport"]["done"] is True
    assert state["rapport"]["cards"] is None
    assert state["controller"]["pending_card_offer"] is None
    assert plan["question"] == ("optional" if provided else "career_concern")
    assert bool(plan["fixed_question"]) is (not provided)


# 첫 안내에서 이미 진로 고민을 말했다면 동의를 다시 요구하지 않고 그 고민부터 다룬다.
def test_concern_at_intro_enters_career_without_reasking_permission():
    state = empty_state()
    plan = C.plan_turn(state, classified(concern_provided=True), 1)
    assert plan["question"] == "optional"
    assert state["conversation_stage"] == "career"
    assert state["career_concern_received"] is True


# 고민 배경을 덧붙였더라도 알아가기 질문에 명시적으로 동의했다면 그 선택을 우선한다.
def test_explicit_consent_with_concern_starts_rapport_and_remembers_concern():
    state = empty_state()
    plan = C.plan_turn(state, classified(rapport_consent=True, concern_provided=True), 1)
    assert state["conversation_stage"] == "rapport"
    assert state["career_concern_received"] is True
    assert plan["rapport_action"]["type"] == "ask"


# 라포 전체 중단 시 미완료 질문·카드를 닫되 기존 진로 확인 대기와 기록은 잃지 않는다.
def test_stop_rapport_preserves_information_but_cancels_remaining_questions_and_cards():
    state = empty_state()
    state["conversation_stage"] = "rapport"
    state["rapport"]["questions"] = [{"id": 17, "status": "asked", "text": "경험", "asks": 1}]
    state["rapport"]["cards"] = new_cards([DECK_BY_ID[1]], "random", 1)
    state["controller"]["pending_card_offer"] = "wrap_up"
    apply_input(state, classified(wants_concern_first=True), 2)
    assert state["rapport"]["questions"][0]["status"] == "skipped"
    assert state["rapport"]["cards"]["dismissed"] is True
    assert state["controller"]["pending_card_offer"] is None
    assert state["conversation_stage"] == "career"


# 한 질문 건너뛰기를 라포 전체 중단으로 해석하지 않는다.
def test_skip_one_question_keeps_rapport_stage():
    state = empty_state()
    state["conversation_stage"] = "rapport"
    state["rapport"]["questions"] = [
        {"id": 17, "status": "asked", "text": "경험", "asks": 1},
        {"id": 11, "status": "pending", "text": "흥미", "asks": 0},
    ]
    apply_input(state, classified(rapport_skipped=True), 2)
    assert state["conversation_stage"] == "rapport"
    assert state["rapport"]["done"] is False
    assert state["rapport"]["questions"][0]["status"] == "skipped"


# 비교 정보 요청에도 필요한 성찰 질문 하나는 허용하며 개인화된 추천 자격과 분리한다.
def test_comparison_answers_then_asks_one_reflection_question():
    state = career_state()
    plan = C.plan_turn(state, classified(request_type="information", concern_provided=True), 2)
    text = "연구와 실무는 다루는 문제와 결과를 확인하는 방식에 차이가 있어요."
    reply = C.finalize_reply(state, plan, generated(content=text))
    assert plan["question"] == "optional"
    assert plan["allow_recommendation"] is False
    assert reply.content.startswith(text)
    assert reply.content.endswith(reply.question)
    assert reply.content.count("?") == 1
    assert reply.suggestions


# 일반 정보 설명에는 질문을 요구하지 않으며, 질문이 없으면 후보 답변도 함께 숨긴다.
def test_information_without_question_keeps_valid_information():
    state = career_state()
    plan = C.plan_turn(state, classified(request_type="information"), 2)
    reply = C.finalize_reply(state, plan, generated(question=None, content="비교 설명입니다."))
    assert reply.content.startswith("비교 설명입니다.")
    assert reply.question is None
    assert reply.content == "비교 설명입니다."
    assert reply.suggestions == []
    assert reply.validation_errors == []
    C.complete_turn(state, plan, reply)
    assert state["controller"]["last_question"] is None


# 설명만 요청하거나 대화를 끝내면 모델이 만든 질문·후보를 제거하고 미결 방향 선택은 보존한다.
@pytest.mark.parametrize("follow_up", ["answer_only", "close"])
def test_answer_only_and_close_do_not_force_questions(follow_up):
    state = career_state()
    state["controller"]["direction"].update(label="연구", turns=10, awaiting_choice=True)
    plan = C.plan_turn(state, classified(request_type="information", follow_up=follow_up), 11)
    reply = C.finalize_reply(state, plan, generated())
    assert plan["question"] == "none"
    assert reply.question is None
    assert reply.suggestions == []
    assert state["controller"]["direction"]["awaiting_choice"] is True
    assert state["controller"]["direction"]["turns"] == 10


# 이미 고민을 받은 상태에서 카드를 고르거나 넘기면 일반적인 고민 질문을 반복하지 않는다.
@pytest.mark.parametrize("selected", [True, False])
def test_card_transition_does_not_reask_known_concern(selected):
    state = career_state()
    state["conversation_stage"] = "cards"
    state["career_concern_received"] = True
    plan = C.plan_turn(state, None, 5, card=DECK_BY_ID[1] if selected else None, career_entry=not selected)
    assert plan["question"] == "optional"
    assert plan["fixed_question"] is None
    assert state["criteria"] == []


# 복원된 대화도 라포·카드 단계이면 후보 답변을 내보내지 않는다.
@pytest.mark.parametrize("stage", ["rapport", "cards"])
def test_restored_rapport_and_card_suggestions_are_hidden(stage):
    state = empty_state()
    state["conversation_stage"] = stage
    assert suggestions_for_stage(state, ["이미 저장된 후보"], has_question=True) == []
    state["conversation_stage"] = "intro"
    assert suggestions_for_stage(state, ["예전의 세 번째 후보"]) == INTRO_SUGGESTIONS


# 구버전 카드 자동 기준을 준비한다. 원문은 보존하고 source 표시로 활성 기준에서만 제외한다.
def legacy_card_state():
    state = career_state()
    card = DECK_BY_ID[12]
    state["rapport"]["cards"] = {**new_cards([card], "random", 1), "chosen_id": 12, "criterion_item_id": 4}
    state["criteria"] = [dict(id=4, text=card["criterion"], evidence="카드 선택", turn=2, status="confirmed")]
    state["experiences"] = [dict(id=3, text="회로 실험", evidence="실험했어요", turn=1, status="confirmed")]
    return state


# 예전 카드 자동 기준은 추천 자격·근거·진로맵에서 제외하고 배경정보로만 모델에 전달한다.
def test_legacy_card_criterion_is_background_not_recommendation_basis():
    state = legacy_card_state()
    plan = C.plan_turn(state, classified(request_type="recommendation"), 3)
    assert state["criteria"][0]["source"] == "rapport_card"
    assert plan["allow_recommendation"] is False
    assert plan["allowed_basis_ids"] == [3]
    assert plan["question"] == "gather_criterion"
    assert active_criteria(state) == []
    prompt = format_state(state)
    assert "[라포에서 선택한 카드 — 배경정보]" in prompt
    assert "id 4:" not in prompt
    career_map = {"criteria": [{"id": 4}], "candidates": [{"name": "연구", "by_criteria": [{"criterion_id": 4}]}]}
    cleaned = without_card_criteria(career_map, state)
    assert cleaned["criteria"] == []
    assert cleaned["candidates"][0]["by_criteria"] == []


# 카드와 연결된 항목이라도 실제 상담에서 정정·확인한 기록이 있으면 사용자 기준으로 보존한다.
def test_legacy_criterion_discussed_in_conversation_is_preserved():
    state = legacy_card_state()
    state["corrections"] = [dict(target_id=4, turn=3, before={"text": "성과", "status": "confirmed"},
                                 after={"text": "구체적인 성과에서 느끼는 보람", "status": "confirmed"}, evidence="맞아요")]
    ensure_flow(state)
    assert state["criteria"][0]["source"] == "conversation"
    assert active_criteria(state)[0]["id"] == 4


# 이후 사용자가 같은 기준을 실제로 말하면 카드 자동 항목과 별개의 대화 근거로 새로 기록할 수 있다.
def test_user_can_confirm_a_criterion_after_legacy_card_selection():
    state = legacy_card_state()
    ensure_flow(state)
    result = classified(new_items=[dict(kind="criterion", text=DECK_BY_ID[12]["criterion"],
                                       evidence="저는 성과가 보이는 게 중요해요", status="confirmed")])
    state, next_id = merge(state, result, 4, 5)
    assert next_id == 6
    assert [i["id"] for i in active_criteria(state)] == [5]


# 카드 설명을 붙인 예전 자동 메시지만 정리하며, 사용자가 실제로 쓴 문장은 유지한다.
def test_only_generated_card_description_is_sanitized_in_model_history():
    state = legacy_card_state()
    card = DECK_BY_ID[12]
    rows = [{"role": "user", "content": f"'{card['name']}' 카드를 골랐어요. ({card['meaning']})"},
            {"role": "user", "content": "저는 성과가 보이는 게 중요해요"}]
    history = history_for_model(rows, state)
    assert history[0]["content"] == f"라포 카드 선택 기록: {card['name']}"
    assert history[1]["content"] == rows[1]["content"]
