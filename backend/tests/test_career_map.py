import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling.career_map import CareerMapError, CareerMapOutput, active_criteria, build_career_map  # noqa: E402
from counseling.state import empty_state  # noqa: E402


def state_with_criteria():
    state = empty_state()
    state["criteria"] = [
        {"id": 1, "text": "연봉", "evidence": "", "turn": 1, "status": "unconfirmed"},
        {"id": 2, "text": "성장 가능성", "evidence": "", "turn": 1, "status": "confirmed"},
        {"id": 3, "text": "울산 근처", "evidence": "", "turn": 1, "status": "withdrawn"},
    ]
    return state


def output(**kw):
    base = {"candidates": [], "next_actions": []}
    base.update(kw)
    return CareerMapOutput.model_validate(base)


def test_withdrawn_criteria_excluded_but_unconfirmed_kept():
    assert [c["id"] for c in active_criteria(state_with_criteria())] == [1, 2]
    cm = build_career_map(state_with_criteria(), output(), last_message_id=10)
    assert [(c["id"], c["status"]) for c in cm["criteria"]] == [(1, "unconfirmed"), (2, "confirmed")]
    assert cm["based_on_message_id"] == 10
    assert cm["updated_at"]


def test_candidate_note_with_unknown_criterion_raises():
    out = output(candidates=[{
        "name": "대학원 진학", "source": "user",
        "by_criteria": [{"criterion_id": 3, "note": "철회된 기준"}],
    }])
    with pytest.raises(CareerMapError):
        build_career_map(state_with_criteria(), out, last_message_id=10)


def test_selected_flag_preserved():
    out = output(next_actions=[
        {"text": "현직자 인터뷰", "selected": True},
        {"text": "관련 프로젝트 참여", "selected": False},
    ])
    cm = build_career_map(state_with_criteria(), out, last_message_id=10)
    assert [a["selected"] for a in cm["next_actions"]] == [True, False]


def test_blank_and_duplicate_notes_are_dropped():
    out = output(candidates=[{
        "name": " 대학원 진학 ", "source": "user",
        "by_criteria": [
            {"criterion_id": 1, "note": "   "},
            {"criterion_id": 2, "note": "첫 번째 설명"},
            {"criterion_id": 2, "note": "중복 설명"},
        ],
    }])
    cm = build_career_map(state_with_criteria(), out, last_message_id=10)
    assert cm["candidates"][0]["name"] == "대학원 진학"
    assert cm["candidates"][0]["by_criteria"] == [{"criterion_id": 2, "note": "첫 번째 설명"}]


def test_blank_candidate_name_or_action_raises():
    with pytest.raises(CareerMapError):
        build_career_map(state_with_criteria(), output(candidates=[
            {"name": "  ", "source": "user", "by_criteria": []},
        ]), last_message_id=10)
    with pytest.raises(CareerMapError):
        build_career_map(state_with_criteria(), output(next_actions=[
            {"text": "", "selected": False},
        ]), last_message_id=10)
