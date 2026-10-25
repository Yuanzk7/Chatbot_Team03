"""상담 상태(state) 스키마와 병합 로직. MVP_Design.md 3.2 / 3.3 참고."""
from copy import deepcopy
from typing import List, Literal, Optional

from pydantic import BaseModel

from rapport import Category, Tag, choose_action, empty_rapport, format_rapport

Status = Literal["confirmed", "unconfirmed", "withdrawn"]
KIND_TO_LIST = {"experience": "experiences", "criterion": "criteria", "constraint": "constraints"}


# --- 분류 호출 출력 스키마 (3.3) ---

class NewItem(BaseModel):
    kind: Literal["experience", "criterion", "constraint"]
    text: str
    evidence: str
    status: Literal["confirmed", "unconfirmed"]


class ItemUpdate(BaseModel):
    target_id: int
    text: Optional[str]
    status: Optional[Status]
    evidence: str


class Classification(BaseModel):
    preferred_name: Optional[str]
    new_items: List[NewItem]
    updates: List[ItemUpdate]
    # 서로 알아가기(라포) 신호. rapport.apply_classification이 사용한다
    rapport_category: Optional[Category]
    concern_tags: List[Tag]
    rapport_answered_ids: List[int]
    rapport_skipped: bool
    wants_concern_first: bool


# --- state ---

TONES = ("friend", "counselor", "spicy")  # prompts.PERSONAS의 키와 같다


def empty_state():
    return {
        "preferred_name": None,
        "tone": "counselor",
        "experiences": [],
        "criteria": [],
        "constraints": [],
        "corrections": [],
        "rapport": empty_rapport(),
    }


class MergeError(Exception):
    """분류 결과가 현재 state와 맞지 않는 경우 (예: 없는 target_id)."""


def merge(state, result: Classification, turn, next_id):
    """분류 결과를 state에 병합한다. (새 state, 다음 항목 id)를 돌려준다.
    항목은 삭제하지 않는다. 정정은 text·status를 바꾸고 corrections에 전후 값을 남긴다."""
    state = deepcopy(state)

    if result.preferred_name:
        state["preferred_name"] = result.preferred_name.strip()

    items_by_id = {
        item["id"]: item
        for key in KIND_TO_LIST.values()
        for item in state[key]
    }

    for u in result.updates:
        item = items_by_id.get(u.target_id)
        if item is None:
            raise MergeError(f"존재하지 않는 항목 id: {u.target_id}")
        before = {"text": item["text"], "status": item["status"]}
        after = {
            "text": u.text.strip() if u.text else item["text"],
            "status": u.status or item["status"],
        }
        if after == before:
            continue
        item["text"], item["status"] = after["text"], after["status"]
        state["corrections"].append({
            "target_id": u.target_id,
            "turn": turn,
            "before": before,
            "after": after,
            "evidence": u.evidence,
        })

    for n in result.new_items:
        key = KIND_TO_LIST[n.kind]
        text = n.text.strip()
        if not text or any(item["text"] == text for item in state[key]):
            continue  # 같은 내용이 이미 있으면 다시 추가하지 않는다
        state[key].append({
            "id": next_id,
            "text": text,
            "evidence": n.evidence,
            "turn": turn,
            "status": n.status,
        })
        next_id += 1

    return state, next_id


# --- 응답 생성 프롬프트용 요약 ---

_LABELS = {"experiences": "경험·관심 활동", "criteria": "선택 기준", "constraints": "현실적 제약"}


def needs_confirmation(state, turn):
    """이번 턴에 unconfirmed로 추가되거나 바뀐 항목의 text 목록."""
    items_by_id = {i["id"]: i for key in KIND_TO_LIST.values() for i in state[key]}
    texts = [
        i["text"]
        for key in KIND_TO_LIST.values()
        for i in state[key]
        if i["turn"] == turn and i["status"] == "unconfirmed"
    ]
    for c in state["corrections"]:
        if c["turn"] == turn and c["after"]["status"] == "unconfirmed":
            text = items_by_id[c["target_id"]]["text"]
            if text not in texts:
                texts.append(text)
    return texts


def plan_turn(state, result: Classification, turn, current_rapport_id=None):
    """이번 턴에 할 질문을 하나만 고른다. 우선순위: 해석·정정 확인 → 사용자 발언 후속 질문 → 라포 질문.
    (직접 요청에 먼저 답하는 것은 질문 선택과 별개로 항상 적용된다.)
    current_rapport_id: 분류 반영 전에 asked 상태였던 라포 질문 id. 그 질문에 답했으면 라포를 이어간다.
    돌려주는 plan: {"question": "confirm"|"follow_up"|"rapport", "confirm_items": [...], "new_texts": [...],
                    "rapport_action": {...}}"""
    confirm_items = needs_confirmation(state, turn)
    new_texts = [n.text for n in result.new_items] + [
        (u.text or "").strip() for u in result.updates if u.text
    ]
    r = state["rapport"]
    # 방금 물은 라포 질문에 내용이 풍부한 답이 오면 질문당 한 번은 꼭 파고든다 (설문처럼 넘어가지 않기 위해).
    # 그 외의 새 이야기에 대한 후속 질문은 연속 두 턴 하지 않는다 (끝없는 되묻기 방지). 알아가기가 끝난 뒤에는 제한 없음
    answered_q = next(
        (q for q in r["questions"] if q["id"] == current_rapport_id and q["id"] in result.rapport_answered_ids),
        None,
    )
    rich_answer = answered_q is not None and bool(new_texts) and not answered_q.get("followed_up")
    follow_up_allowed = r["done"] or rich_answer or r.get("last_follow_up_turn") != turn - 1
    if confirm_items:
        question = "confirm"
    elif new_texts and follow_up_allowed:
        question = "follow_up"
        if rich_answer:
            answered_q["followed_up"] = True
        if not r["done"]:
            r["last_follow_up_turn"] = turn
    else:
        question = "rapport"
    action = choose_action(r, turn, hold=question != "rapport")
    return {"question": question, "confirm_items": confirm_items, "new_texts": new_texts, "rapport_action": action}


def format_turn_plan(plan):
    """'이번 턴의 질문' 지시. 질문은 한 턴에 하나만."""
    lines = ["## 이번 턴의 질문 (하나만)"]
    q = plan["question"]
    if q == "confirm":
        lines.append("[이번 턴 질문] 다음 항목은 사용자의 말을 해석한 것이라 뜻이 확실하지 않다: " + "; ".join(plan["confirm_items"]))
        lines.append(
            "이번 답변의 질문은 이 해석이 맞는지 확인하는 질문 하나로 한다 "
            "(예: \"경제적 독립은 중요하지만, 빨리 이룰 필요는 없다는 뜻인가요?\"). 다른 질문은 하지 않는다."
        )
    elif q == "follow_up":
        lines.append("[이번 턴 질문] 사용자가 방금 꺼낸 이야기(새로 드러난 내용: " + "; ".join(plan["new_texts"]) + ")를 되짚고, "
                     "그 내용을 구체화하는 후속 질문 하나만 한다 (예: 관련 경험, 그렇게 느낀 이유). 다른 질문은 하지 않는다.")
    elif q == "card_focus":
        card = plan["card"]
        lines.append(
            f"[이번 턴 질문] 사용자가 라포 카드 '{card['name']}'({card['meaning']})를 골랐다. 선택을 반갑게 받아들이고, "
            "이 기준을 진로 탐색의 중심으로 삼아 그 기준이 드러났던 경험이나 상황을 구체화하는 질문 하나를 한다. 다른 질문은 하지 않는다."
        )
    else:
        lines.append("[이번 턴 질문] 위 '서로 알아가기' 지시를 따른다. 지시에 질문이 없으면 사용자의 말에 이어지는 탐색 질문 하나를 한다.")
    return "\n".join(lines)


def format_state(state, turn=None, plan=None):
    """응답 호출의 시스템 프롬프트에 붙일 현재 상태 요약. withdrawn 항목은 활성 목록에서 제외한다.
    plan(plan_turn 결과)을 주면 '서로 알아가기' 진행과 '이번 턴의 질문' 지시를 함께 붙인다."""
    lines = []
    if plan is not None:
        lines.append(format_rapport(state["rapport"], plan["rapport_action"]))
        lines.append("")
        lines.append(format_turn_plan(plan))
        lines.append("")
    lines.append("## 현재까지 확인한 사용자 정보")
    lines.append(f"호칭: {state['preferred_name'] or '(아직 확인 안 됨)'}")
    for key, label in _LABELS.items():
        confirmed = [i for i in state[key] if i["status"] == "confirmed"]
        unconfirmed = [i for i in state[key] if i["status"] == "unconfirmed"]
        lines.append(f"[확인된 {label}] " + ("; ".join(i["text"] for i in confirmed) if confirmed else "없음"))
        lines.append(f"[미확인 {label}] " + ("; ".join(i["text"] for i in unconfirmed) if unconfirmed else "없음"))
    if state["corrections"]:
        lines.append("[정정 이력]")
        for c in state["corrections"]:
            lines.append(
                f"- {c['turn']}턴: \"{c['before']['text']}\" {c['before']['status']} → "
                f"\"{c['after']['text']}\" {c['after']['status']}"
            )
    cards = state.get("rapport", {}).get("cards")
    if cards and cards.get("chosen_id"):
        chosen = next((c for c in cards["offered"] if c["id"] == cards["chosen_id"]), None)
        if chosen:
            lines.append(
                f"[중심 기준 카드] {chosen['name']}: {chosen['meaning']}. 사용자가 직접 고른 기준이므로 탐색의 중심으로 삼되, "
                "다른 기준도 열어 둔다."
            )
    lines.append(
        "미확인 항목은 확정된 기준처럼 쓰지 말고, 조언에 활용하기 전에 사용자에게 확인한다. "
        "정정 이력에서 withdrawn으로 바뀐 항목은 대화에 남아 있더라도 현재 기준으로 사용하지 않는다."
    )
    return "\n".join(lines)
