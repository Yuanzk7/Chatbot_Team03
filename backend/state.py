"""상담 상태(state) 스키마와 병합 로직. MVP_Design.md 3.2 / 3.3 참고."""
from copy import deepcopy
from typing import List, Literal, Optional

from pydantic import BaseModel

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


# --- state ---

def empty_state():
    return {
        "preferred_name": None,
        "experiences": [],
        "criteria": [],
        "constraints": [],
        "corrections": [],
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


def format_state(state, turn=None):
    """응답 호출의 시스템 프롬프트에 붙일 현재 상태 요약. withdrawn 항목은 활성 목록에서 제외한다.
    turn을 주면 이번 턴에 미확인이 된 항목을 따로 묶어 확인 질문을 지시한다."""
    lines = ["## 현재까지 확인한 사용자 정보"]
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
    lines.append(
        "미확인 항목은 확정된 기준처럼 쓰지 말고, 조언에 활용하기 전에 사용자에게 확인한다. "
        "정정 이력에서 withdrawn으로 바뀐 항목은 대화에 남아 있더라도 현재 기준으로 사용하지 않는다."
    )
    pending = needs_confirmation(state, turn) if turn is not None else []
    if pending:
        lines.append("[이번 턴에 확인이 필요한 항목] " + "; ".join(pending))
        lines.append(
            "이 항목은 사용자의 말을 해석한 것이라 뜻이 확실하지 않다. 이번 답변의 질문은 이 해석이 맞는지 "
            "확인하는 질문으로 한다 (예: \"경제적 독립은 중요하지만, 빨리 이룰 필요는 없다는 뜻인가요?\")."
        )
    return "\n".join(lines)
