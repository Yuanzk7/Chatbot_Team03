"""라포 형성 컨트롤러: 질문 데이터, 선별, 진행 상태 갱신, 다음 행동 선택.
모델 호출 없이 동작하며, 모델이 보고한 질문 id로 상태를 갱신한다 (본문과의 일치는 테스트에서 확인)."""
import random
from copy import deepcopy
from typing import List, Literal, Optional

Category = Literal["daily", "taste", "relationship", "values", "unist"]
Tag = Literal["interest", "values", "workstyle", "relationship", "stability", "academic", "future", "daily"]

CATEGORY_LABELS = {"daily": "일상", "taste": "취향", "relationship": "관계", "values": "가치관", "unist": "학교생활"}
TAG_LABELS = {
    "interest": "흥미·몰입", "values": "가치관·우선순위", "workstyle": "일하는 방식·성향",
    "relationship": "관계·협업", "stability": "안정·도전", "academic": "학업·전공",
    "future": "미래상·목표", "daily": "일상·스트레스",
}

MAX_QUESTIONS = 5       # 상한이지 필수 수량이 아니다
MAX_ASKS = 2            # 같은 질문을 이 횟수까지 물었는데 답이 없으면 건너뛴 것으로 본다
MAX_HOLDS = 2           # 사용자가 다른 이야기를 꺼내 같은 질문을 이 횟수만큼 보류하면 건너뛴 것으로 본다
DEFAULT_OPENER_ID = 17  # 범주·태그를 판정할 정보가 부족할 때 시작하는 가벼운 질문

def _q(i, category, tags, text):
    return {"id": i, "category": category, "tags": tags, "text": text}

QUESTIONS = [
    _q(1, "daily", ["daily", "workstyle"], "아침형 인간이야, 밤형 인간이야?"),
    _q(2, "daily", ["daily", "interest"], "혼자 있을 때 주로 뭐 하면서 시간 보내?"),
    _q(3, "daily", ["daily"], "하루 중에 가장 좋아하는 시간은 언제야?"),
    _q(4, "daily", ["daily", "interest"], "아무 일정 없는 휴일에는 보통 뭐 해?"),
    _q(5, "daily", ["workstyle"], "계획을 세워서 움직이는 편이야, 즉흥적인 편이야?"),
    _q(6, "daily", ["daily", "values"], "요즘 일상에서 가장 소소하게 행복한 순간은 언제야?"),
    _q(7, "daily", ["daily"], "요즘 가장 자주 먹는 음식이나 좋아하는 음식은 뭐야?"),
    _q(8, "daily", ["daily"], "스트레스 받을 때 주로 어떻게 풀어?"),
    _q(9, "daily", ["daily", "workstyle"], "최근에 새로 생긴 습관이나 바꾸고 싶은 습관이 있어?"),
    _q(10, "daily", ["daily", "interest"], "요즘 가장 기다려지는 일이 뭐야?"),
    _q(11, "taste", ["interest"], "요즘 가장 재미있게 하고 있는 게 뭐야?"),
    _q(12, "taste", ["interest", "future"], "시간이랑 돈이 충분하다면 꼭 해보고 싶은 취미가 있어?"),
    _q(13, "taste", ["interest"], "좋아하는 영화나 드라마 장르가 뭐야?"),
    _q(14, "taste", ["interest"], "요즘 가장 자주 듣는 노래나 좋아하는 가수는 누구야?"),
    _q(15, "taste", ["interest"], "여행을 간다면 가장 가보고 싶은 곳은 어디야?"),
    _q(16, "taste", ["stability", "workstyle"], "여행에서는 새로운 경험을 하는 게 좋아, 편하게 쉬는 게 좋아?"),
    _q(17, "taste", ["interest"], "지금까지 해본 것 중에 가장 재미있었던 경험은 뭐야?"),
    _q(18, "taste", ["values", "interest"], "남들은 좋아하는데 너는 별로 안 좋아하는 게 있어?"),
    _q(19, "taste", ["interest", "values"], "반대로 남들은 별로 관심 없는데 너는 유독 좋아하는 게 있어?"),
    _q(20, "taste", ["interest"], "어릴 때부터 지금까지 꾸준히 좋아하는 게 있어?"),
    _q(21, "relationship", ["relationship"], "처음 보는 사람이랑 금방 친해지는 편이야?"),
    _q(22, "relationship", ["relationship"], "친한 친구들이랑 있을 때는 어떤 모습이야?"),
    _q(23, "relationship", ["relationship", "values"], "친구를 사귈 때 가장 중요하게 생각하는 게 뭐야?"),
    _q(24, "relationship", ["relationship", "workstyle"], "어떤 성격의 사람이랑 가장 잘 맞는 것 같아?"),
    _q(25, "relationship", ["relationship", "values"], "반대로 어떤 행동을 하는 사람이랑은 잘 안 맞아?"),
    _q(26, "relationship", ["relationship", "workstyle"], "고민이 생기면 주변 사람에게 이야기하는 편이야, 혼자 해결하는 편이야?"),
    _q(27, "relationship", ["relationship", "daily"], "기분이 안 좋을 때 혼자 있는 게 좋아, 누가 옆에 있어주는 게 좋아?"),
    _q(28, "relationship", ["relationship"], "친구나 주변 사람한테 가장 고마웠던 순간은 언제야?"),
    _q(29, "relationship", ["relationship"], "사람들이 너를 처음 봤을 때랑 친해진 후에 다르다고 하는 점이 있어?"),
    _q(30, "relationship", ["values", "future"], "주변 사람들이 너를 어떤 사람으로 기억해 줬으면 좋겠어?"),
    _q(31, "values", ["values"], "요즘 너한테 가장 중요한 게 뭐야?"),
    _q(32, "values", ["values", "daily"], "어떤 순간에 '오늘 하루 잘 보냈다'는 생각이 들어?"),
    _q(33, "values", ["values"], "돈, 시간, 인간관계 중에 지금 가장 중요하게 생각하는 건 뭐야?"),
    _q(34, "values", ["stability", "values"], "무언가를 선택할 때 안정적인 것과 새로운 도전 중에 어느 쪽에 더 끌려?"),
    _q(35, "values", ["values", "future"], "지금까지 살면서 가장 잘했다고 생각하는 선택은 뭐야?"),
    _q(36, "values", ["future", "interest"], "살면서 꼭 한 번은 해보고 싶은 일이 있어?"),
    _q(37, "values", ["values", "future"], "어떤 사람을 보면 멋있거나 존경스럽다고 느껴?"),
    _q(38, "values", ["values", "future"], "네가 생각하는 행복한 삶은 어떤 모습이야?"),
    _q(39, "values", ["future"], "5년 뒤에는 어떤 일상을 보내고 있었으면 좋겠어?"),
    _q(40, "values", ["values"], "지금의 너에게 가장 소중하고 잃고 싶지 않은 건 뭐야?"),
    _q(41, "unist", ["academic", "values"], "UNIST에 오기로 결정한 가장 큰 이유가 뭐야?"),
    _q(42, "unist", ["academic", "daily"], "UNIST에 와서 가장 좋았던 점은 뭐야?"),
    _q(43, "unist", ["academic", "daily"], "반대로 UNIST 생활에서 가장 아쉽거나 힘든 점은 뭐야?"),
    _q(44, "unist", ["daily"], "학교에서 가장 좋아하는 장소는 어디야?"),
    _q(45, "unist", ["academic", "interest"], "지금까지 들었던 수업 중에 가장 재미있었던 수업은 뭐야?"),
    _q(46, "unist", ["academic", "interest"], "전공 공부를 하면서 의외로 재미있다고 느꼈던 분야가 있어?"),
    _q(47, "unist", ["interest", "relationship"], "학교에서 공부 말고 가장 재미있게 했던 활동은 뭐야?"),
    _q(48, "unist", ["future", "interest"], "대학생활이 끝나기 전에 꼭 해보고 싶은 게 있어?"),
    _q(49, "unist", ["future"], "졸업 후에 어떤 일을 하면서 살고 싶어?"),
    _q(50, "unist", ["academic", "values"], "UNIST에 들어오기 전이랑 지금을 비교하면 가장 많이 달라진 점이 뭐야?"),
]
QUESTIONS_BY_ID = {q["id"]: q for q in QUESTIONS}

Status = Literal["pending", "asked", "answered", "skipped"]


def empty_rapport():
    return {
        "category": None,       # 모델이 판정한 범주 (내부 분류)
        "tags": [],             # 고민에서 드러난 주제 태그
        "questions": [],        # {id, text, status, asks}
        "done": False,
        "end_reason": None,     # completed | user_requested
        "done_turn": None,      # done으로 바뀐 턴 (그 턴에만 마무리 지시를 넣는다)
    }


def _entry(q):
    return {"id": q["id"], "text": q["text"], "status": "pending", "asks": 0, "holds": 0}


def select_questions(category, tags, exclude_ids, limit, rng=random):
    """범주 일치 2점 + 태그 겹침 1점씩. 0점은 제외. 동점은 무작위. 최대 limit개."""
    scored = []
    for q in QUESTIONS:
        if q["id"] in exclude_ids:
            continue
        score = (2 if category and q["category"] == category else 0) + len(set(q["tags"]) & set(tags))
        if score > 0:
            scored.append((score, rng.random(), q))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [q for _, _, q in scored[:limit]]


def current_question(rapport):
    return next((q for q in rapport["questions"] if q["status"] == "asked"), None)


def _finish(rapport, reason, turn):
    rapport["done"] = True
    rapport["end_reason"] = reason
    rapport["done_turn"] = turn


def apply_classification(rapport, signals, turn, rng=random):
    """분류 호출 결과(signals: rapport_category, concern_tags, rapport_answered_ids, rapport_skipped,
    wants_concern_first)를 반영한다. 모델이 보고한 id는 이 상담에서 뽑힌 질문에 속할 때만 쓴다."""
    r = deepcopy(rapport)
    if r["done"]:
        return r

    if signals.wants_concern_first:
        _finish(r, "user_requested", turn)
        return r

    if r["category"] is None and signals.rapport_category:
        r["category"] = signals.rapport_category
    for t in signals.concern_tags:
        if t not in r["tags"]:
            r["tags"].append(t)

    # 질문 선별: 정보가 생기면 최대 5개까지 채우고, 정보가 없으면 가벼운 질문 하나로 시작
    have = {q["id"] for q in r["questions"]}
    if len(r["questions"]) < MAX_QUESTIONS and (r["category"] or r["tags"]):
        for q in select_questions(r["category"], r["tags"], have, MAX_QUESTIONS - len(r["questions"]), rng):
            r["questions"].append(_entry(q))
    elif not r["questions"]:
        r["questions"].append(_entry(QUESTIONS_BY_ID[DEFAULT_OPENER_ID]))

    by_id = {q["id"]: q for q in r["questions"]}
    for qid in signals.rapport_answered_ids:
        q = by_id.get(qid)
        if q and q["status"] in ("pending", "asked"):
            q["status"] = "answered"
    cur = current_question(r)
    if signals.rapport_skipped and cur:  # 건너뛰기는 현재 질문에만 적용
        cur["status"] = "skipped"

    if r["questions"] and all(q["status"] in ("answered", "skipped") for q in r["questions"]):
        _finish(r, "completed", turn)
    return r


def _skip_current_and_check(r, cur, turn):
    """현재 질문을 건너뛴 것으로 처리하고, 전부 끝났으면 completed로 마친다. 마쳤으면 True."""
    cur["status"] = "skipped"
    if all(q["status"] in ("answered", "skipped") for q in r["questions"]):
        _finish(r, "completed", turn)
        return True
    return False


def choose_action(rapport, turn, hold=False):
    """응답 생성 전에 다음 행동을 고른다. 답이 없는 현재 질문을 먼저 처리하고, 너무 여러 번 물은 질문은 건너뛴다.
    hold=True면 이번 턴에는 다른 질문(해석 확인·사용자 발언 후속)을 하므로 라포 질문을 보류한다.
    돌려주는 action: {"type": "ask"|"continue"|"hold"|"wrap_up"|"concern"|"none", "question": {...}|None}"""
    r = rapport
    if r["done"]:
        if r["done_turn"] == turn:
            return {"type": "wrap_up" if r["end_reason"] == "completed" else "concern", "question": None}
        return {"type": "none", "question": None}

    cur = current_question(r)
    if hold:
        # 보류가 거듭되면 사용자의 흐름을 따라가고 그 질문은 놓아준다
        if cur:
            cur["holds"] = cur.get("holds", 0) + 1
            if cur["holds"] >= MAX_HOLDS and _skip_current_and_check(r, cur, turn):
                return {"type": "wrap_up", "question": None}
        return {"type": "hold", "question": None}

    if cur and cur["asks"] >= MAX_ASKS:
        if _skip_current_and_check(r, cur, turn):
            return {"type": "wrap_up", "question": None}
        cur = None
    if cur:
        return {"type": "continue", "question": cur}
    nxt = next((q for q in r["questions"] if q["status"] == "pending"), None)
    if nxt:
        return {"type": "ask", "question": nxt}
    return {"type": "none", "question": None}


def apply_reply(rapport, action, asked_question_id):
    """모델이 실제로 물었다고 보고한 경우에만 asked로 바꾼다. 지시한 질문 id 외의 값, 보류 턴의 보고는 무시한다."""
    q = action.get("question")
    if action["type"] in ("ask", "continue") and q and asked_question_id == q["id"]:
        q["status"] = "asked"
        q["asks"] += 1


def progress(rapport):
    qs = rapport["questions"]
    return {
        "answered": sum(q["status"] == "answered" for q in qs),
        "skipped": sum(q["status"] == "skipped" for q in qs),
        "remaining": sum(q["status"] in ("pending", "asked") for q in qs),
        "total": len(qs),
    }


def format_rapport(rapport, action):
    """응답 프롬프트에 붙일 '서로 알아가기' 지시."""
    lines = ["## 서로 알아가기 (라포 질문)"]
    p = progress(rapport)
    if rapport["questions"]:
        lines.append(f"진행: 답함 {p['answered']}, 건너뜀 {p['skipped']}, 남음 {p['remaining']} (최대 {MAX_QUESTIONS}개)")
    t = action["type"]
    q = action.get("question")
    if t == "ask":
        lines.append(
            f"[이번 턴 지시] 사용자의 말을 되짚은 뒤, 다음 질문을 자연스럽게 하나만 묻는다 (id {q['id']}): \"{q['text']}\" "
            "사용자가 직접 질문을 했으면 먼저 답하고 이 질문은 다음 턴으로 미뤄도 된다. "
            f"물었으면 asked_question_id에 {q['id']}를, 묻지 않았으면 null을 적는다."
        )
    elif t == "continue":
        lines.append(
            f"[이번 턴 지시] 직전에 물은 질문 (id {q['id']}) \"{q['text']}\"에 사용자가 아직 답하지 않았다. "
            "사용자의 직접 요청이 있으면 먼저 처리한다. 그다음 같은 문장을 반복하지 말고, 답할 기회를 주거나 넘어가도 되는지 가볍게 묻는다. "
            f"다시 물었으면 asked_question_id에 {q['id']}를, 아니면 null을 적는다."
        )
    elif t == "wrap_up":
        lines.append(
            "[이번 턴 지시] 알아가기 단계가 끝났다. 지금까지 알게 된 사용자의 경험과 기준을 2~3문장으로 되짚고, "
            "진로 탐색 질문으로 자연스럽게 넘어간다. asked_question_id는 null."
        )
    elif t == "concern":
        lines.append(
            "[이번 턴 지시] 사용자가 알아가기 질문 대신 바로 진로 고민으로 들어가길 원한다. 알아가기 질문을 하지 않는다. "
            "고민에 공감하고, 지금 가장 고민되는 선택이나 상황과 관련 경험을 편하게 말하도록 묻는다. asked_question_id는 null."
        )
    elif t == "hold":
        lines.append("[이번 턴 지시] 알아가기 질문은 이번 턴에 보류한다. 아래 '이번 턴의 질문'을 따른다. asked_question_id는 null.")
    else:
        lines.append("[이번 턴 지시] 알아가기 질문은 하지 않는다. asked_question_id는 null.")
    if t in ("ask", "continue"):
        lines.append("알아가기 질문을 물을 때는 예시 답변(suggestions)의 마지막을 \"이 질문은 건너뛸게요\"로 둔다.")
    return "\n".join(lines)
