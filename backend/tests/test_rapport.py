import random
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling import rapport as R  # noqa: E402


def signals(**kw):
    base = dict(rapport_category=None, concern_tags=[], rapport_answered_ids=[], rapport_skipped=False,
                wants_concern_first=False)
    base.update(kw)
    return SimpleNamespace(**base)


def rng():
    return random.Random(0)


def test_question_data_is_consistent():
    ids = [q["id"] for q in R.QUESTIONS]
    assert ids == list(range(1, 51))
    assert all(q["tags"] for q in R.QUESTIONS)
    assert R.DEFAULT_OPENER_ID in R.QUESTIONS_BY_ID


def test_select_prefers_category_and_tag_overlap_without_duplicates():
    picked = R.select_questions("values", ["stability"], exclude_ids=set(), limit=5, rng=rng())
    ids = [q["id"] for q in picked]
    assert len(ids) == 5 and len(set(ids)) == 5
    assert ids[0] == 34  # 가치관 범주 + 안정·도전 태그 → 최고 점수
    assert not any(q["light"] for q in picked)


def test_light_questions_only_when_nothing_else_scores():
    picked = R.select_questions("taste", ["interest"], exclude_ids=set(), limit=5, rng=rng())
    assert not {13, 14, 15} & {q["id"] for q in picked}  # 영화·노래·여행지는 뒤로 밀린다
    only_light = R.select_questions("daily", [], exclude_ids={2, 4, 5, 6, 8, 9, 10}, limit=5, rng=rng())
    assert only_light == []  # 가벼운 질문만 남으면 0점이라 뽑지 않는다 → 기본 질문으로 시작


def test_no_info_starts_with_default_opener():
    r = R.apply_classification(R.empty_rapport(), signals(), turn=1, rng=rng())
    assert [q["id"] for q in r["questions"]] == [R.DEFAULT_OPENER_ID]
    assert r["category"] is None and not r["done"]


def test_info_later_fills_up_to_max():
    r = R.apply_classification(R.empty_rapport(), signals(), turn=1, rng=rng())
    r = R.apply_classification(r, signals(rapport_category="unist", concern_tags=["academic"]), turn=2, rng=rng())
    assert len(r["questions"]) == R.MAX_QUESTIONS
    assert r["questions"][0]["id"] == R.DEFAULT_OPENER_ID  # 기존 질문은 유지
    assert len({q["id"] for q in r["questions"]}) == R.MAX_QUESTIONS


def test_concern_first_ends_rapport_without_questions():
    r = R.apply_classification(R.empty_rapport(), signals(wants_concern_first=True), turn=1, rng=rng())
    assert r["done"] and r["end_reason"] == "user_requested" and r["questions"] == []
    assert R.choose_action(r, 1)["type"] == "concern"
    assert R.choose_action(r, 2)["type"] == "none"


def seeded():
    r = R.apply_classification(R.empty_rapport(), signals(rapport_category="values", concern_tags=["stability"]),
                               turn=1, rng=rng())
    return r


def test_ask_then_continue_then_auto_skip():
    r = seeded()
    a1 = R.choose_action(r, 1)
    assert a1["type"] == "ask"
    R.apply_reply(r, a1, a1["question"]["id"])
    assert R.current_question(r)["id"] == a1["question"]["id"]
    # 사용자가 답하지 않음 → 같은 질문 이어가기
    r = R.apply_classification(r, signals(), turn=2, rng=rng())
    a2 = R.choose_action(r, 2)
    assert a2["type"] == "continue" and a2["question"]["id"] == a1["question"]["id"]
    R.apply_reply(r, a2, a2["question"]["id"])
    # 두 번 물어도 답이 없으면 건너뛴 것으로 보고 다음 질문으로
    r = R.apply_classification(r, signals(), turn=3, rng=rng())
    a3 = R.choose_action(r, 3)
    assert a3["type"] == "ask" and a3["question"]["id"] != a1["question"]["id"]
    assert r["questions"][0]["status"] == "skipped"


def test_reported_id_must_match_instructed_question():
    r = seeded()
    a = R.choose_action(r, 1)
    R.apply_reply(r, a, 999)
    assert R.current_question(r) is None
    R.apply_reply(r, a, None)
    assert R.current_question(r) is None


def test_answered_ids_only_for_selected_questions_and_passing_answers():
    r = seeded()
    a = R.choose_action(r, 1)
    R.apply_reply(r, a, a["question"]["id"])
    other = next(q["id"] for q in r["questions"] if q["status"] == "pending")
    r = R.apply_classification(r, signals(rapport_answered_ids=[a["question"]["id"], other, 999]), turn=2, rng=rng())
    by_id = {q["id"]: q for q in r["questions"]}
    assert by_id[a["question"]["id"]]["status"] == "answered"
    assert by_id[other]["status"] == "answered"  # 지나가며 답한 질문도 처리
    assert 999 not in by_id


def test_skip_applies_only_to_current_question():
    r = seeded()
    r2 = R.apply_classification(r, signals(rapport_skipped=True), turn=2, rng=rng())
    assert all(q["status"] == "pending" for q in r2["questions"])  # 물은 질문이 없으면 무시
    a = R.choose_action(r, 1)
    R.apply_reply(r, a, a["question"]["id"])
    r3 = R.apply_classification(r, signals(rapport_skipped=True), turn=2, rng=rng())
    assert R.current_question(r3) is None
    assert sum(q["status"] == "skipped" for q in r3["questions"]) == 1


def test_completion_triggers_wrap_up_once():
    r = seeded()
    ids = [q["id"] for q in r["questions"]]
    r = R.apply_classification(r, signals(rapport_answered_ids=ids), turn=4, rng=rng())
    assert r["done"] and r["end_reason"] == "completed" and r["done_turn"] == 4
    assert R.choose_action(r, 4)["type"] == "wrap_up"
    assert R.choose_action(r, 5)["type"] == "none"
    assert "알아가기 단계가 끝났다" in R.format_rapport(r, R.choose_action(r, 4))


def test_hold_keeps_question_and_skips_after_repeated_holds():
    r = seeded()
    a = R.choose_action(r, 1)
    R.apply_reply(r, a, a["question"]["id"])
    qid = a["question"]["id"]
    h1 = R.choose_action(r, 2, hold=True)
    assert h1["type"] == "hold" and R.current_question(r)["id"] == qid and R.current_question(r)["asks"] == 1
    R.apply_reply(r, h1, qid)  # 보류 턴에 모델이 id를 보고해도 무시
    assert R.current_question(r)["asks"] == 1
    h2 = R.choose_action(r, 3, hold=True)
    assert h2["type"] == "hold"
    assert R.current_question(r) is None  # 두 번 보류되면 건너뜀
    assert next(q for q in r["questions"] if q["id"] == qid)["status"] == "skipped"


def test_hold_without_current_question_consumes_nothing():
    r = seeded()
    h = R.choose_action(r, 1, hold=True)
    assert h["type"] == "hold"
    assert all(q["status"] == "pending" for q in r["questions"])


def test_turn_limit_wraps_up_even_if_questions_remain():
    r = seeded()
    r = R.apply_classification(r, signals(), turn=R.TURN_LIMIT - 1, rng=rng())
    assert not r["done"]
    r = R.apply_classification(r, signals(), turn=R.TURN_LIMIT, rng=rng())
    assert r["done"] and r["end_reason"] == "turn_limit"
    assert R.choose_action(r, R.TURN_LIMIT)["type"] == "wrap_up"
    assert R.choose_action(r, R.TURN_LIMIT + 1)["type"] == "none"
