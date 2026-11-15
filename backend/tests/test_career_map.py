import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling.career_map import (  # noqa: E402
    CareerMapError, CareerMapOutput, active_criteria, build_career_map, confirmed_experiences,
)
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


def test_model_cannot_mark_an_unconfirmed_action_as_selected():
    out = output(next_actions=[
        {"text": "현직자 인터뷰", "selected": True},
        {"text": "관련 프로젝트 참여", "selected": False},
    ])
    cm = build_career_map(state_with_criteria(), out, last_message_id=10)
    assert [a["selected"] for a in cm["next_actions"]] == [False, False]


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


# 진로맵 맨 위의 사용자 소개는 앞뒤 공백을 정리해 담고, 소개가 없는 출력도 그대로 처리한다.
def test_profile_is_trimmed_and_optional():
    cm = build_career_map(state_with_criteria(), output(profile="  영상 편집을 즐기는 분이에요.  "), last_message_id=10)
    assert cm["profile"] == "영상 편집을 즐기는 분이에요."
    assert build_career_map(state_with_criteria(), output(), last_message_id=10)["profile"] == ""


# 소개의 근거로는 대화에서 확인한 경험만 전달하고, 미확인 경험과 카드 자동 항목은 뺀다.
def test_confirmed_experiences_exclude_unconfirmed_and_card_items():
    state = empty_state()
    state["experiences"] = [
        {"id": 1, "text": "영상 편집", "evidence": "", "turn": 1, "status": "confirmed"},
        {"id": 2, "text": "연구 인턴", "evidence": "", "turn": 2, "status": "unconfirmed"},
        {"id": 3, "text": "카드 경험", "evidence": "", "turn": 1, "status": "confirmed", "source": "rapport_card"},
    ]
    assert confirmed_experiences(state) == ["영상 편집"]
