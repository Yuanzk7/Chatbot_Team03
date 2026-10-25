import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state import Classification, MergeError, empty_state, format_state, merge, needs_confirmation  # noqa: E402


def classification(**kw):
    base = {"preferred_name": None, "new_items": [], "updates": []}
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
    assert "[이번 턴에 확인이 필요한 항목] 경제적 독립" in format_state(state, 2)
    assert "이번 턴에 확인이 필요한 항목" not in format_state(state, 3)
    assert "이번 턴에 확인이 필요한 항목" not in format_state(state)


def test_new_unconfirmed_item_this_turn_is_flagged():
    state, next_id = seeded()  # id 2는 1턴에 unconfirmed로 추가됨
    assert needs_confirmation(state, 1) == ["혼자 문제 해결"]
