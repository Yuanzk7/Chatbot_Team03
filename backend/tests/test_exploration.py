"""진행 단계, 정정·건너뛰기, 실제 행동 동의와 상위 통제의 관계를 모델 호출 없이 검증한다."""
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling import exploration as E  # noqa: E402
from counseling.career_map import CareerMapOutput, build_career_map  # noqa: E402
from counseling.controller import complete_turn, finalize_reply, plan_turn  # noqa: E402
from counseling.state import Classification, empty_state, merge  # noqa: E402
from llm.client import Reply  # noqa: E402


# 실제 분류 스키마를 사용하되 각 사례에서 필요한 사용자 신호만 변경한다.
def classified(exploration=None, **changes):
    fields = dict(preferred_name=None, new_items=[], updates=[], rapport_category=None, concern_tags=[],
                  rapport_answered_ids=[], rapport_skipped=False, wants_concern_first=False,
                  request_type="reflection", direction_relation="same", direction_label=None,
                  direction_choice="none", needs_reason=False, follow_up="reflect",
                  rapport_consent=False, concern_provided=True, exploration=exploration)
    fields.update(changes)
    return Classification(**fields)


# 진행 신호의 무변경 필드를 채워, 의도한 입력만 단계 판정에 영향을 주도록 한다.
def progress(**changes):
    fields = dict(concern=None, focus=None, conditions="unchanged", defer=None, candidates=[], actions=[])
    fields.update(changes)
    return fields


# 진로 상담 중인 상태를 준비한다. 기준과 후보는 이미 사용자가 말한 내용이다.
def career_state(criteria=True, candidates=True):
    state = empty_state()
    state.update(conversation_stage="career", career_concern_received=True)
    state["rapport"].update(done=True, done_turn=0, end_reason="completed")
    state["controller"]["direction"].update(label="대학원과 취업", turns=1)
    state["experiences"] = [dict(id=1, text="모델 구현", status="confirmed", evidence="구현해봤어", turn=1)]
    if criteria:
        state["criteria"] = [dict(id=2, text="연구 역량 습득", status="confirmed", evidence="중요해", turn=1)]
    state["exploration"]["concern"] = "석사 2년 동안 원하는 배움을 얻을 수 있을까"
    if candidates:
        state["exploration"]["candidates"] = [dict(name=name, source="user", active=True) for name in ("대학원", "취업")]
    return state


# 응답 생성 이후와 동일하게 구조화 응답을 검증·조립하고 실제 전달 기록을 반영한다.
def deliver(state, plan, **changes):
    fields = dict(content="말씀하신 내용을 살펴볼게요.", question=None, suggestions=[],
                  asked_question_id=None, rapport_cards=[], basis_ids=[], recommendations=[], exploration=None)
    fields.update(changes)
    reply = finalize_reply(state, plan, Reply(**fields))
    complete_turn(state, plan, reply)
    return reply


# 기준·조건이 이미 있으면 재수집하지 않고 비교하며, 비교를 실제 전달한 다음에 행동으로 이동한다.
def test_known_information_comparison_and_next_action_form_one_flow():
    state = career_state()
    state["exploration"]["conditions_status"] = "needed"
    assert plan_turn(state, classified(), 2)["exploration"]["focus"] == "conditions"
    state, _ = merge(state, classified(new_items=[dict(kind="constraint", text="2년 투자 가능",
                            evidence="2년은 투자할 수 있어", status="confirmed")]), 3, 3)
    plan = plan_turn(state, classified(), 3)
    assert plan["exploration"]["focus"] == "comparison"
    assert state["exploration"]["conditions_status"] == "known"
    assert state["exploration"]["comparison"] is None
    reply = deliver(state, plan, content="대학원과 취업을 원하는 배움의 관점에서 살펴봤어요.",
                    exploration=dict(compared_candidates=["대학원", "취업"], proposed_actions=[]))
    assert reply.validation_errors == []
    assert state["exploration"]["comparison"]["turn"] == 3
    assert plan_turn(state, classified(), 4)["exploration"]["focus"] == "action"


# 아직 모르는 조건과 없다고 확인한 조건을 구분하되 두 경우 모두 필수 설문으로 만들지 않는다.
@pytest.mark.parametrize("status", ["unknown", "not_needed"])
def test_conditions_are_not_forced_without_a_reason(status):
    state = career_state()
    state["exploration"]["conditions_status"] = status
    assert plan_turn(state, classified(), 2)["exploration"]["focus"] == "comparison"
    assert state["exploration"]["conditions_status"] == status


# 조건 확인을 미루면 다음으로 진행하고, 사용자가 다시 요청할 때 해당 주제로 돌아온다.
def test_defer_and_resume_conditions():
    state = career_state()
    state["exploration"]["conditions_status"] = "needed"
    state, _ = merge(state, classified(progress(defer="conditions")), 2, 3)
    plan = plan_turn(state, classified(), 2)
    assert plan["exploration"]["focus"] == "comparison"
    assert state["exploration"]["conditions_status"] == "deferred"
    state, _ = merge(state, classified(progress(focus="conditions")), 3, 3)
    assert plan_turn(state, classified(), 3)["exploration"]["focus"] == "conditions"
    assert "conditions" not in state["exploration"]["deferred_topics"]


# 기준을 정정하면 이전 비교를 그대로 완료로 간주하지 않으며 기존 경험은 보존한다.
def test_corrected_basis_reopens_comparison():
    state = career_state()
    deliver(state, plan_turn(state, classified(), 2), content="대학원과 취업의 차이를 비교했어요.",
            exploration=dict(compared_candidates=["대학원", "취업"], proposed_actions=[]))
    assert plan_turn(state, classified(), 3)["exploration"]["focus"] == "action"
    state, _ = merge(state, classified(updates=[dict(target_id=2, text="직접 제품을 만드는 경험",
                       status="confirmed", evidence="제품을 만들어보고 싶은 게 더 중요해")]), 4, 3)
    assert plan_turn(state, classified(), 4)["exploration"]["focus"] == "comparison"
    assert state["experiences"][0]["text"] == "모델 구현"


# 검증을 통과해 실제 표시한 추천 후보도 다음 비교에 활용하되 아직 비교를 마친 것으로 보지 않는다.
def test_delivered_recommendations_become_candidates_without_completing_comparison():
    state = career_state(candidates=False)
    reply = deliver(state, plan_turn(state, classified(request_type="recommendation"), 2),
                    recommendations=[dict(candidate=name, reason="직접 구현한 경험과 배움의 기준을 함께 살펴볼 수 있어요.",
                                          basis_ids=[1, 2]) for name in ("연구 개발", "제품 개발")])
    assert reply.validation_errors == []
    assert [c["source"] for c in state["exploration"]["candidates"]] == ["assistant", "assistant"]
    assert state["exploration"]["comparison"] is None
    assert plan_turn(state, classified(), 3)["exploration"]["focus"] == "comparison"


# 방향 점검이 우선인 턴에는 행동 제안·비교 완료를 기록하지 않고 사용자의 진행 요청을 보존한다.
def test_checkpoint_takes_priority_without_losing_pending_focus():
    state = career_state()
    state["controller"]["direction"]["turns"] = 9
    state, _ = merge(state, classified(progress(focus="action")), 2, 3)
    plan = plan_turn(state, classified(), 2)
    assert plan["question"] == "direction_check" and plan["exploration"] is None
    deliver(state, plan, exploration=dict(compared_candidates=["대학원", "취업"], proposed_actions=["논문 찾아보기"]))
    assert state["exploration"]["actions"] == []
    assert state["exploration"]["comparison"] is None
    assert state["exploration"]["requested_focus"] == "action"
    follow = plan_turn(state, classified(direction_choice="continue"), 3)
    assert follow["exploration"]["focus"] == "action"


# 검증 실패로 대체한 응답은 비교나 행동을 전달한 것으로 기록하지 않는다.
def test_rejected_reply_does_not_complete_a_stage():
    state = career_state()
    state, _ = merge(state, classified(progress(focus="comparison")), 2, 3)
    reply = deliver(state, plan_turn(state, classified(), 2), basis_ids=[999],
                    exploration=dict(compared_candidates=["대학원", "취업"], proposed_actions=["논문 찾아보기"]))
    assert reply.validation_errors
    assert state["exploration"]["actions"] == [] and state["exploration"]["comparison"] is None
    assert state["exploration"]["requested_focus"] == "comparison"


# 정보 설명은 현재 단계의 기준 질문으로 대체되지 않으며 이미 요청한 두 내용을 함께 전달한다.
def test_information_request_keeps_its_answer_without_a_stage_question():
    state = career_state(criteria=False)
    plan = plan_turn(state, classified(request_type="information"), 2)
    assert plan["exploration"]["information"] is True
    content = "데이터 구성과 분석 방법을 함께 살펴보면 연구 질문을 검증하는 과정을 알 수 있어요."
    reply = deliver(state, plan, content=content)
    assert reply.content == content and reply.question is None and reply.validation_errors == []


# 같은 상태에서 질문을 반복하지 않고, 사용자가 행동 선택을 미루면 제안과 질문도 재촉하지 않는다.
def test_repeated_questions_and_deferred_actions_are_suppressed():
    state = career_state(criteria=False, candidates=False)
    plan = plan_turn(state, classified(), 2)
    deliver(state, plan, question="어떤 배움을 원하시나요?")
    assert plan_turn(state, classified(), 3)["exploration"]["focus"] == "action"
    state, _ = merge(state, classified(progress(defer="action")), 4, 3)
    reply = deliver(state, plan_turn(state, classified(), 4), question="논문을 찾아보실까요?",
                    exploration=dict(compared_candidates=[], proposed_actions=["논문 찾아보기"]))
    assert reply.question is None and state["exploration"]["actions"] == []


# 행동 제안은 표시했을 때만 저장하며 요약 모델의 selected=true가 사용자 선택을 대신하지 않는다.
def test_proposal_and_explicit_decision_are_distinct_in_the_map():
    state = career_state()
    state, _ = merge(state, classified(progress(focus="action")), 2, 3)
    reply = deliver(state, plan_turn(state, classified(), 2),
                    exploration=dict(compared_candidates=[], proposed_actions=["연구실 논문 찾아보기"]))
    action = state["exploration"]["actions"][0]
    assert action["text"] in reply.content and action["status"] == "proposed"
    output = CareerMapOutput(candidates=[], next_actions=[dict(text=action["text"], selected=True)])
    assert build_career_map(state, output, 10)["next_actions"][0]["selected"] is False
    message = "이번 주에 연구실 논문을 찾아볼게요"
    state, _ = merge(state, classified(progress(actions=[dict(action_id=action["id"], text=None,
                     status="selected", evidence=message)])), 3, 3, user_message=message)
    assert build_career_map(state, CareerMapOutput(candidates=[], next_actions=[]), 12)["next_actions"] == [
        dict(text="연구실 논문 찾아보기", selected=True)]


# 질문·공감 없이 행동만 제안하는 정상 응답도 코드가 표시하고 기록할 수 있다.
def test_actions_only_reply_is_rendered_before_empty_reply_validation():
    state = career_state()
    state, _ = merge(state, classified(progress(focus="action")), 2, 3)
    reply = deliver(state, plan_turn(state, classified(), 2), content="",
                    exploration=dict(compared_candidates=[], proposed_actions=["연구실 논문 찾아보기"]))
    assert reply.validation_errors == []
    assert "연구실 논문 찾아보기" in reply.content
    assert state["exploration"]["actions"][0]["status"] == "proposed"


# 제안이 여러 개일 때의 짧은 동의나 실제 발언에 없는 근거로 선택을 추측하지 않는다.
@pytest.mark.parametrize("evidence", ["좋아요", "실제로 말하지 않은 결정"])
def test_ambiguous_or_unquoted_action_decisions_are_ignored(evidence):
    state = career_state()
    state, _ = merge(state, classified(progress(focus="action")), 2, 3)
    deliver(state, plan_turn(state, classified(), 2), question="어떤 일을 해보고 싶으신가요?",
            exploration=dict(compared_candidates=[], proposed_actions=["논문 읽기", "재학생에게 물어보기"]))
    state, _ = merge(state, classified(progress(actions=[dict(action_id=1, text=None,
                     status="selected", evidence=evidence)])), 3, 3, user_message="좋아요")
    assert all(a["status"] == "proposed" for a in state["exploration"]["actions"])


# 직전에 질문과 함께 행동 하나만 제안했다면 짧은 동의를 그 행동에 연결할 수 있다.
def test_short_consent_can_select_one_recent_proposal():
    state = career_state()
    state, _ = merge(state, classified(progress(focus="action")), 2, 3)
    deliver(state, plan_turn(state, classified(), 2), question="논문을 한 편 찾아보실까요?",
            exploration=dict(compared_candidates=[], proposed_actions=["논문 읽기"]))
    assert E.classification_context(state)["action_confirmation_id"] == 1
    state, _ = merge(state, classified(progress(actions=[dict(action_id=1, text=None, status="selected",
                     evidence="좋아요")])), 3, 3, user_message="좋아요")
    assert state["exploration"]["actions"][0]["status"] == "selected"


# 진로맵을 새로 열어 제안 목록이 바뀌었으면 이전 질문에 대한 짧은 호응을 행동 선택으로 재사용하지 않는다.
def test_summary_refresh_clears_ambiguous_old_action_confirmation():
    state = career_state()
    state, _ = merge(state, classified(progress(focus="action")), 2, 3)
    deliver(state, plan_turn(state, classified(), 2), question="논문을 찾아보실까요?",
            exploration=dict(compared_candidates=[], proposed_actions=["논문 읽기"]))
    assert E.classification_context(state)["action_confirmation_id"] == 1
    E.record_summary(state, [dict(text="재학생에게 물어보기", selected=False)], 2)
    assert E.classification_context(state)["action_confirmation_id"] is None


# 철회한 행동은 모델이 다시 추천해도 진로맵과 새 제안에서 제외한다.
def test_withdrawn_action_is_not_reintroduced_by_the_model():
    state = career_state()
    E.record_summary(state, [dict(text="논문 읽기", selected=False)], 1)
    state, _ = merge(state, classified(progress(actions=[dict(action_id=1, text=None, status="withdrawn",
                     evidence="논문 읽기는 하지 않을래")])), 2, 3, user_message="논문 읽기는 하지 않을래")
    reply = deliver(state, plan_turn(state, classified(), 2),
                    exploration=dict(compared_candidates=[], proposed_actions=["논문 읽기"]))
    assert "해볼 수 있는 일" not in reply.content
    assert E.map_actions(state, [dict(text="논문 읽기", selected=True)]) == []


# 새 방향에는 이전 후보·행동을 자동 적용하지 않으며 기존 기록과 공통 기준은 남겨 둔다.
def test_direction_change_preserves_records_but_resets_current_scope():
    state = career_state()
    E.record_summary(state, [dict(text="연구실 알아보기", selected=False)], 1)
    before = deepcopy(state)
    state, _ = merge(state, classified(progress(concern="개발 직무 탐색"), direction_relation="new",
                     direction_label="개발 직무"), 2, 3)
    assert state["criteria"] == before["criteria"]
    assert state["exploration"]["candidates"] and all(not c["active"] for c in state["exploration"]["candidates"])
    assert state["exploration"]["actions"] and E.map_actions(state, []) == []
    assert state["exploration"]["concern"] == "개발 직무 탐색"


# 이름이 없는 모호한 방향 신호만으로 기존 후보·행동을 초기화하지 않는다.
def test_unclear_new_direction_preserves_current_scope():
    state = career_state()
    before = deepcopy(state["exploration"])
    state, _ = merge(state, classified(direction_relation="new", direction_label=None), 2, 3)
    assert state["exploration"] == before


# 새 방향 이름을 아직 말하지 않았어도 명시적으로 전환하면 이전 후보를 현재 비교에 자동 사용하지 않는다.
def test_explicit_switch_resets_scope_before_new_direction_is_named():
    state = career_state()
    state["controller"]["direction"]["awaiting_choice"] = True
    state, _ = merge(state, classified(direction_choice="switch", direction_relation="none"), 2, 3)
    assert all(not c["active"] for c in state["exploration"]["candidates"])
    assert state["criteria"][0]["status"] == "confirmed"


# 다른 방향에서 철회했던 행동을 새 방향에서도 영구적으로 금지하지 않는다. 새 제안은 선택되지 않은 상태다.
def test_old_withdrawal_does_not_block_a_proposal_for_a_new_direction():
    state = career_state()
    E.record_summary(state, [dict(text="논문 읽기", selected=False)], 1)
    state["exploration"]["actions"][0]["status"] = "withdrawn"
    state, _ = merge(state, classified(direction_relation="new", direction_label="다른 연구 분야"), 2, 3)
    assert E.map_actions(state, [dict(text="논문 읽기", selected=True)]) == [dict(text="논문 읽기", selected=False)]
