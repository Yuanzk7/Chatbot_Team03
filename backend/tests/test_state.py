import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling.state import Classification, MergeError, empty_state, format_state, merge, needs_confirmation, plan_turn  # noqa: E402


def classification(**kw):
    base = {
        "preferred_name": None, "new_items": [], "updates": [],
        "rapport_category": None, "concern_tags": [], "rapport_answered_ids": [],
        "rapport_skipped": False, "wants_concern_first": False,
    }
    base.update(kw)
    return Classification.model_validate(base)


def seeded():
    """기준 1개(id 1, confirmed), 경험 1개(id 2, unconfirmed)가 있는 state."""
    state, next_id = merge(
        empty_state(),
        classification(new_items=[
            {"kind": "criterion", "text": "연봉", "evidence": "연봉이 중요해", "status": "confirmed"},
            {"kind": "experience", "text": "혼자 문제 해결", "evidence": "혼자 푸는 게 좋아", "status": "unconfirmed"},
        ]),
        turn=1,
        next_id=1,
    )
    return state, next_id


def test_add_items_assigns_ids_and_turn():
    state, next_id = seeded()
    assert next_id == 3
    assert state["criteria"] == [{"id": 1, "text": "연봉", "evidence": "연봉이 중요해", "turn": 1, "status": "confirmed"}]
    assert state["experiences"][0]["id"] == 2
    assert state["corrections"] == []


def test_duplicate_text_not_added_again():
    state, next_id = seeded()
    state, next_id = merge(state, classification(new_items=[
        {"kind": "criterion", "text": "연봉", "evidence": "다시 연봉", "status": "confirmed"},
    ]), turn=2, next_id=next_id)
    assert len(state["criteria"]) == 1
    assert next_id == 3


def test_unconfirmed_to_confirmed_records_correction():
    state, next_id = seeded()
    state, _ = merge(state, classification(updates=[
        {"target_id": 2, "text": None, "status": "confirmed", "evidence": "응, 맞아"},
    ]), turn=2, next_id=next_id)
    assert state["experiences"][0]["status"] == "confirmed"
    assert state["corrections"] == [{
        "target_id": 2, "turn": 2,
        "before": {"text": "혼자 문제 해결", "status": "unconfirmed"},
        "after": {"text": "혼자 문제 해결", "status": "confirmed"},
        "evidence": "응, 맞아",
    }]


def test_not_sure_becomes_unconfirmed_not_deleted():
    state, next_id = seeded()
    state, _ = merge(state, classification(updates=[
        {"target_id": 1, "text": None, "status": "unconfirmed", "evidence": "정말 중요한지 모르겠어"},
    ]), turn=2, next_id=next_id)
    assert len(state["criteria"]) == 1
    assert state["criteria"][0]["status"] == "unconfirmed"


def test_withdraw_keeps_item_and_excludes_from_active_summary():
    state, next_id = seeded()
    state, _ = merge(state, classification(updates=[
        {"target_id": 1, "text": None, "status": "withdrawn", "evidence": "연봉은 중요하지 않아"},
    ]), turn=2, next_id=next_id)
    assert state["criteria"][0]["status"] == "withdrawn"
    summary = format_state(state)
    assert "[확인된 선택 기준] 없음" in summary
    assert "[미확인 선택 기준] 없음" in summary
    assert "withdrawn" in summary  # 정정 이력에는 남는다


def test_text_change_records_before_after():
    state, next_id = seeded()
    state, _ = merge(state, classification(updates=[
        {"target_id": 1, "text": "경제적 독립", "status": None, "evidence": "사실은 빨리 독립하고 싶어서"},
    ]), turn=3, next_id=next_id)
    assert state["criteria"][0]["text"] == "경제적 독립"
    assert state["criteria"][0]["status"] == "confirmed"
    c = state["corrections"][0]
    assert c["before"]["text"] == "연봉" and c["after"]["text"] == "경제적 독립"


def test_unknown_target_id_raises():
    state, next_id = seeded()
    with pytest.raises(MergeError):
        merge(state, classification(updates=[
            {"target_id": 99, "text": None, "status": "confirmed", "evidence": "응"},
        ]), turn=2, next_id=next_id)


def test_empty_result_changes_nothing():
    state, next_id = seeded()
    new_state, new_next = merge(state, classification(), turn=2, next_id=next_id)
    assert new_state == state and new_next == next_id


def test_noop_update_records_no_correction():
    state, next_id = seeded()
    new_state, _ = merge(state, classification(updates=[
        {"target_id": 1, "text": None, "status": "confirmed", "evidence": "연봉이 중요해"},
    ]), turn=2, next_id=next_id)
    assert new_state["corrections"] == []


def test_preferred_name_set_and_kept():
    state, next_id = seeded()
    state, next_id = merge(state, classification(preferred_name="유찬"), turn=2, next_id=next_id)
    state, _ = merge(state, classification(preferred_name=None), turn=3, next_id=next_id)
    assert state["preferred_name"] == "유찬"


def test_partial_retraction_flags_item_for_confirmation_this_turn_only():
    state, next_id = seeded()
    state, _ = merge(state, classification(updates=[
        {"target_id": 1, "text": "경제적 독립", "status": "unconfirmed", "evidence": "꼭 빠를 필요는 없을 것 같아"},
    ]), turn=2, next_id=next_id)
    assert needs_confirmation(state, 2) == ["경제적 독립"]
    assert needs_confirmation(state, 3) == []
    plan = plan_turn(state, classification(), 2)
    assert plan["question"] == "confirm" and plan["rapport_action"]["type"] == "hold"
    text = format_state(state, 2, plan)
    assert "확인하는 질문 하나로 한다" in text and "경제적 독립" in text
    assert plan_turn(state, classification(), 3)["question"] == "rapport"
    assert "이번 턴의 질문" not in format_state(state)


def test_new_unconfirmed_item_this_turn_is_flagged():
    state, next_id = seeded()  # id 2는 1턴에 unconfirmed로 추가됨
    assert needs_confirmation(state, 1) == ["혼자 문제 해결"]


def test_plan_prefers_follow_up_when_user_brings_new_content():
    state, _ = seeded()
    result = classification(new_items=[
        {"kind": "criterion", "text": "재미있게 할 수 있는 일", "evidence": "재밌는 일이 중요해", "status": "confirmed"},
    ])
    plan = plan_turn(state, result, 2, current_rapport_id=None)
    assert plan["question"] == "follow_up" and "재미있게 할 수 있는 일" in plan["new_texts"]
    assert "후속 질문 하나만" in format_state(state, 2, plan)


def test_follow_up_once_then_next_rapport_question():
    state, _ = seeded()
    rich = classification(
        new_items=[{"kind": "experience", "text": "유럽 한 달 여행", "evidence": "유럽에서 한 달", "status": "confirmed"}],
        rapport_answered_ids=[17],
    )
    # 라포 답에 내용이 풍부하면 먼저 한 번 파고든다
    assert plan_turn(state, rich, 2, current_rapport_id=17)["question"] == "follow_up"
    assert state["rapport"]["last_follow_up_turn"] == 2
    # 바로 다음 턴에 또 새 내용이 와도 이번엔 라포 질문으로 넘어간다
    again = classification(new_items=[{"kind": "experience", "text": "현지 친구", "evidence": "친구를 사귀었어요", "status": "confirmed"}])
    assert plan_turn(state, again, 3)["question"] == "rapport"
    # 그다음 턴에는 다시 후속 질문이 가능하다
    assert plan_turn(state, again, 4)["question"] == "follow_up"


def test_follow_ups_unlimited_after_rapport_done():
    state, _ = seeded()
    state["rapport"]["done"] = True
    state["rapport"]["end_reason"] = "completed"
    state["rapport"]["done_turn"] = 3
    new = classification(new_items=[{"kind": "experience", "text": "동아리", "evidence": "동아리에서", "status": "confirmed"}])
    assert plan_turn(state, new, 5)["question"] == "follow_up"
    assert plan_turn(state, new, 6)["question"] == "follow_up"


def test_plan_confirm_beats_follow_up():
    state, next_id = seeded()
    state, _ = merge(state, classification(updates=[
        {"target_id": 1, "text": None, "status": "unconfirmed", "evidence": "모르겠어"},
    ]), turn=2, next_id=next_id)
    result = classification(new_items=[
        {"kind": "experience", "text": "동아리 활동", "evidence": "동아리에서", "status": "confirmed"},
    ])
    assert plan_turn(state, result, 2)["question"] == "confirm"


def test_rich_answer_to_asked_question_always_gets_one_follow_up():
    state, _ = seeded()
    state["rapport"]["questions"] = [{"id": 17, "text": "가장 재미있었던 경험", "status": "asked", "asks": 1, "holds": 0}]
    state["rapport"]["last_follow_up_turn"] = 1  # 직전 턴에 이미 후속 질문을 했어도
    rich = classification(
        new_items=[{"kind": "experience", "text": "유럽 한 달 여행", "evidence": "유럽에서 한 달", "status": "confirmed"}],
        rapport_answered_ids=[17],
    )
    assert plan_turn(state, rich, 2, current_rapport_id=17)["question"] == "follow_up"
    q17 = next(q for q in state["rapport"]["questions"] if q["id"] == 17)
    assert q17["followed_up"] is True
    # 같은 질문의 답을 또 파고들진 않는다
    state["rapport"]["last_follow_up_turn"] = 2
    assert plan_turn(state, rich, 3, current_rapport_id=17)["question"] == "rapport"
