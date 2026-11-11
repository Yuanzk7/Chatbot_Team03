"""시작 동의, 자유응답 라포, 상담 전환과 카드 배경정보의 경계를 모델 없이 검증한다."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling import controller as C  # noqa: E402
from counseling.cards import DECK_BY_ID, new_cards  # noqa: E402
from counseling.career_map import active_criteria  # noqa: E402
from counseling.flow import (  # noqa: E402
    INTRO_SUGGESTIONS, apply_input, ensure_flow, enter_career, history_for_model, suggestions_for_stage,
    without_card_criteria,
)
from counseling.state import Classification, empty_state, format_state, merge  # noqa: E402
from llm.client import Reply  # noqa: E402


# 실제 분류 스키마를 사용해 요청 유형과 후속 질문 의도를 서로 독립적으로 지정한다.
def classified(**changes):
    fields = dict(preferred_name=None, new_items=[], updates=[], rapport_category=None, concern_tags=[],
                  rapport_answered_ids=[], rapport_skipped=False, wants_concern_first=False,
                  request_type="reflection", needs_current_info=False, direction_relation="none", direction_label=None,
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


# 이전 상담의 동의 단계가 남아 있어도 알아가기를 건너뛰는 후보는 내보내지 않는다.
def test_legacy_intro_offers_only_consent_suggestion():
    state = empty_state()
    plan = C.plan_turn(state, classified(), 1)
    reply = C.finalize_reply(state, plan, generated())
    assert state["conversation_stage"] == "intro"
    assert state["rapport"]["questions"] == []
    assert reply.suggestions == ["네, 여쭤보세요"]


# 동의하면 라포를 시작하지만, 그때부터 모델이 만든 후보 답변은 저장·표시하지 않는다.
def test_consent_starts_free_text_rapport_without_suggestions():
    state = empty_state()
    plan = C.plan_turn(state, classified(rapport_consent=True), 1)
    reply = C.finalize_reply(state, plan, generated())
    assert state["conversation_stage"] == "rapport"
    assert plan["rapport_action"]["type"] == "ask"
    assert state["controller"]["direction"]["turns"] == 0
    assert reply.suggestions == []


# 상담을 재촉하지 않고 고민만 먼저 말했다면 알아가기를 이어가고, 말한 고민은 기억한다.
@pytest.mark.parametrize("provided", [True, False])
def test_rapport_continues_when_user_shares_concern_first(provided):
    state = empty_state()
    state["conversation_stage"] = "rapport"
    state["rapport"]["questions"] = [{"id": 17, "status": "asked", "text": "경험", "asks": 1, "holds": 0},
                                     {"id": 11, "status": "pending", "text": "흥미", "asks": 0, "holds": 0}]
    C.plan_turn(state, classified(concern_provided=provided, request_type="information"), 2)
    assert state["conversation_stage"] == "rapport"
    assert state["rapport"]["done"] is False
    assert state["career_concern_received"] is provided


# 이전 상담의 동의 단계에서 고민을 먼저 말하면 동의로 보고 알아가기를 시작하며 고민은 기억한다.
def test_concern_at_legacy_intro_starts_rapport():
    state = empty_state()
    plan = C.plan_turn(state, classified(concern_provided=True), 1)
    assert state["conversation_stage"] == "rapport"
    assert state["career_concern_received"] is True
    assert plan["rapport_action"]["type"] == "ask"


# 고민 배경을 덧붙였더라도 알아가기 질문에 명시적으로 동의했다면 그 선택을 우선한다.
def test_explicit_consent_with_concern_starts_rapport_and_remembers_concern():
    state = empty_state()
    plan = C.plan_turn(state, classified(rapport_consent=True, concern_provided=True), 1)
    assert state["conversation_stage"] == "rapport"
    assert state["career_concern_received"] is True
    assert plan["rapport_action"]["type"] == "ask"


# 상담을 재촉하면 남은 질문을 줄여 바로 카드 단계로 넘기되, 카드 없이 진로 상담을 시작하지는 않는다.
def test_request_for_counseling_moves_to_cards_not_career():
    state = empty_state()
    state["conversation_stage"] = "rapport"
    state["rapport"]["questions"] = [{"id": 17, "status": "asked", "text": "경험", "asks": 1, "holds": 0},
                                     {"id": 11, "status": "pending", "text": "흥미", "asks": 0, "holds": 0}]
    plan = C.plan_turn(state, classified(wants_concern_first=True, concern_provided=True,
                                       follow_up="answer_only", request_type="information"), 2)
    assert plan["rapport_action"]["type"] == "wrap_up"
    assert state["conversation_stage"] == "cards"
    assert state["career_concern_received"] is True
    assert "career_started_turn" not in state


# 카드 단계에서 고민을 입력해도 상담으로 넘어가지 않는다. 상담은 카드를 확인하는 버튼으로만 시작한다.
def test_concern_at_card_stage_does_not_start_career():
    state = empty_state()
    state["conversation_stage"] = "cards"
    state["rapport"].update(done=True, done_turn=1, end_reason="completed")
    state["rapport"]["cards"] = new_cards([DECK_BY_ID[1]], "conversation", 1)
    apply_input(state, classified(concern_provided=True), 2)
    assert state["conversation_stage"] == "cards"
    assert state["career_concern_received"] is True
    assert "career_started_turn" not in state


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


# 이미 고민을 받은 상태에서 카드를 확인하고 상담을 시작하면 일반적인 고민 질문을 반복하지 않는다.
def test_card_transition_does_not_reask_known_concern():
    state = career_state()
    state["conversation_stage"] = "cards"
    state["career_concern_received"] = True
    plan = C.plan_turn(state, None, 5, card=DECK_BY_ID[1])
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
    assert "[라포 카드 — 챗봇이 대화를 보고 고른 배경정보]" in prompt
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


# 진로 상담이 시작된 턴은 처음 한 번만 기록하고, 다시 호출돼도 바꾸지 않는다.
def test_enter_career_records_start_turn_once():
    state = empty_state()
    enter_career(state, 4)
    assert state["room_switches"] == [{"room": "career", "turn": 4}]
    enter_career(state, 6)
    assert state["career_started_turn"] == 4


# 진로 상담 뒤에는 알아가기 구간을 질문·답변 원문과 카드 선택 사실로만 전달하고 챗봇의 정리 문장은 뺀다.
def test_career_history_replaces_rapport_with_verbatim_record():
    state = career_state()
    card = DECK_BY_ID[6]
    state["rapport"]["cards"] = {**new_cards([card], "conversation", 3), "chosen_id": 6}
    state["career_started_turn"] = 4
    rows = [
        {"turn": 0, "role": "assistant", "content": "몇 가지 여쭤봐도 될까요?"},
        {"turn": 1, "role": "user", "content": "네, 여쭤보세요"},
        {"turn": 1, "role": "assistant", "content": "가장 재미있었던 경험은 무엇인가요?"},
        {"turn": 2, "role": "user", "content": "유럽여행을 한달 동안 다녀왔어"},
        {"turn": 2, "role": "assistant", "content": "낯선 곳의 기억이 오래 남겠네요.\n\n특히 기억에 남는 순간은 무엇이었나요?"},
        {"turn": 3, "role": "user", "content": "현지 사람들과 이야기한 것"},
        {"turn": 3, "role": "assistant", "content": "사람과 문화를 탐구하는 성향이 보여요. 아래 카드 중 하나를 골라 주세요."},
        {"turn": 4, "role": "user", "content": "진로 상담을 시작할게요."},
        {"turn": 4, "role": "assistant", "content": "지금 진로와 관련해서 어떤 고민이 있으신가요?"},
    ]
    history = history_for_model(rows, state)
    record = history[0]["content"]
    assert history[0]["role"] == "developer"
    assert "- 질문: 가장 재미있었던 경험은 무엇인가요?\n  답변: 유럽여행을 한달 동안 다녀왔어" in record
    assert "- 질문: 특히 기억에 남는 순간은 무엇이었나요?\n  답변: 현지 사람들과 이야기한 것" in record
    assert f"라포 카드: '{card['name']}' (챗봇이 대화를 보고 고른 카드)" in record
    assert "낯선 곳의 기억" not in record and "탐구하는 성향" not in record
    assert history[1:] == [{"role": r["role"], "content": r["content"]} for r in rows[7:]]


# 알아가기 답변 없이 바로 상담을 시작했다면 기존처럼 전체 기록을 그대로 전달한다.
def test_career_history_without_rapport_answers_is_unchanged():
    state = career_state()
    state["career_started_turn"] = 1
    rows = [{"turn": 0, "role": "assistant", "content": "몇 가지 여쭤봐도 될까요?"},
            {"turn": 1, "role": "user", "content": "바로 고민부터 말할게요"}]
    assert history_for_model(rows, state) == [{"role": r["role"], "content": r["content"]} for r in rows]


# 알아가기 중에 '질문은 그만'처럼 설명만 원해도 알아가기는 중단되지 않고 다음 질문을 이어간다.
def test_answer_only_in_rapport_still_asks_next_question():
    state = empty_state()
    state["conversation_stage"] = "rapport"
    state["rapport"]["questions"] = [{"id": 17, "status": "answered", "text": "경험", "asks": 1, "holds": 0},
                                     {"id": 11, "status": "pending", "text": "흥미", "asks": 0, "holds": 0}]
    plan = C.plan_turn(state, classified(follow_up="answer_only", concern_provided=True), 2)
    assert plan["rapport_action"]["type"] == "ask"
    assert plan["rapport_action"]["question"]["id"] == 11


# 알아가기 중에는 끝낸다는 말을 막고, 진로 이야기를 미룬다는 안내는 실제로 고민을 말한 턴에만 붙인다.
@pytest.mark.parametrize("concern,expects_notice", [(False, False), (True, True)])
def test_rapport_turn_plan_mentions_counseling_only_when_concern_is_raised(concern, expects_notice):
    state = empty_state()
    state["conversation_stage"] = "rapport"
    state["rapport"]["questions"] = [{"id": 17, "status": "answered", "text": "경험", "asks": 1, "holds": 0},
                                     {"id": 11, "status": "pending", "text": "흥미", "asks": 0, "holds": 0}]
    text = C.format_turn_plan(C.plan_turn(state, classified(concern_provided=concern), 2))
    assert "알아가기를 끝낸다는 말은 하지 않는다" in text
    assert ("진로 상담에서 다룬다" in text) is expects_notice


# 알아가기 중 필수 후속 질문이 빠지면 진로 고민을 묻지 않고 이야기를 더 듣는 질문으로 보완한다.
def test_missing_follow_up_in_rapport_uses_rapport_fallback_question():
    state = empty_state()
    state["conversation_stage"] = "rapport"
    state["rapport"]["questions"] = [{"id": 17, "status": "answered", "text": "경험", "asks": 1, "holds": 0},
                                     {"id": 11, "status": "pending", "text": "흥미", "asks": 0, "holds": 0}]
    result = classified(new_items=[{"kind": "experience", "text": "맛있는 음식 먹기", "evidence": "맛있는 걸 먹어",
                                    "status": "confirmed"}])
    state, _ = merge(state, result, 2, 1)
    plan = C.plan_turn(state, result, 2)
    assert plan["question"] == "follow_up"
    reply = C.finalize_reply(state, plan, generated(question=None, content="맛있는 걸 드시며 푸시는군요."))
    assert reply.question == C.RAPPORT_FALLBACK_QUESTION
    assert "고민" not in reply.content


# 알아가기를 두 번 했다면 진로 상담에는 두 알아가기의 원문 기록 하나와 진로 상담 구간만 전달하고,
# 다시 하는 중에는 이번 알아가기 구간만 전달한다.
def test_history_after_restarted_rapport_keeps_rooms_apart():
    state = career_state()
    state["career_started_turn"] = 2
    state["room_switches"] = [{"room": "career", "turn": 2}, {"room": "rapport", "turn": 4},
                              {"room": "career", "turn": 6}]
    rows = [
        {"turn": 0, "role": "assistant", "content": "가장 재미있었던 경험은 무엇인가요?"},
        {"turn": 1, "role": "user", "content": "유럽 여행"},
        {"turn": 1, "role": "assistant", "content": "아래 카드를 보여드릴게요."},
        {"turn": 2, "role": "user", "content": "진로 상담을 시작할게요."},
        {"turn": 2, "role": "assistant", "content": "어떤 고민이 있으신가요?"},
        {"turn": 3, "role": "user", "content": "대학원 분야를 못 정했어요"},
        {"turn": 3, "role": "assistant", "content": "분야별 연구 방식을 비교해 볼 수 있어요."},
        {"turn": 4, "role": "user", "content": "알아가기를 다시 할게요."},
        {"turn": 4, "role": "assistant", "content": "다시 알아가 볼게요.\n\n혼자 있을 때 주로 무엇을 하시나요?"},
        {"turn": 5, "role": "user", "content": "게임을 해요"},
        {"turn": 5, "role": "assistant", "content": "카드를 아래에 보여드릴게요."},
        {"turn": 6, "role": "user", "content": "인공지능 쪽이 궁금해요"},
    ]
    history = history_for_model(rows, state)
    record = history[0]["content"]
    assert history[0]["role"] == "developer"
    assert "답변: 유럽 여행" in record and "- 질문: 혼자 있을 때 주로 무엇을 하시나요?\n  답변: 게임을 해요" in record
    assert "알아가기를 다시 할게요." not in record
    assert [h["content"] for h in history[1:]] == [
        "진로 상담을 시작할게요.", "어떤 고민이 있으신가요?", "대학원 분야를 못 정했어요",
        "분야별 연구 방식을 비교해 볼 수 있어요.", "인공지능 쪽이 궁금해요"]
    state["conversation_stage"] = "rapport"
    state["room_switches"] = state["room_switches"][:2]
    assert [h["content"] for h in history_for_model(rows[:10], state)] == [
        "알아가기를 다시 할게요.", "다시 알아가 볼게요.\n\n혼자 있을 때 주로 무엇을 하시나요?", "게임을 해요"]
