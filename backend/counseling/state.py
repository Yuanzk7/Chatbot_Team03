"""상담 상태(state) 스키마와 병합 로직. MVP_Design.md 3.2 / 3.3 참고."""
from copy import deepcopy
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from counseling import exploration
from counseling.exploration import ExplorationInput
from counseling.controller import empty_controller, format_turn_plan
from counseling.flow import conversation_items, ensure_flow
from counseling.rapport import Category, Tag, empty_rapport, format_rapport

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
    # 요청과 탐색 방향은 모델이 분류하고, 카운터·행동은 controller가 결정한다.
    request_type: Literal["information", "recommendation", "reflection"]
    direction_relation: Literal["same", "new", "unclear", "none"]
    direction_label: Optional[str] = None
    direction_choice: Literal["continue", "switch", "unsure", "none"]
    # 이전 미확인 항목 중 이번 조언과 관련 있는 항목과, 사용자가 유보한 항목을 구분한다.
    relevant_item_ids: List[int] = Field(default_factory=list)
    deferred_item_ids: List[int] = Field(default_factory=list)
    needs_reason: bool
    # reflect는 상담을 이어간다는 뜻이며 일반 후속 질문을 강제하지 않는다.
    follow_up: Literal["reflect", "answer_only", "close"]
    rapport_consent: bool
    concern_provided: bool
    exploration: Optional[ExplorationInput] = None


# --- state ---

# 상담 시작 시 사용할 빈 상태를 만든다. 상담 규칙 상태도 함께 저장해 새로고침 후 이어간다.
def empty_state():
    return {
        "preferred_name": None,
        "conversation_stage": "intro",
        "career_concern_received": False,
        "experiences": [],
        "criteria": [],
        "constraints": [],
        "corrections": [],
        "rapport": empty_rapport(),
        "controller": empty_controller(),
        "exploration": exploration.empty_exploration(),
    }


class MergeError(Exception):
    """분류 결과가 현재 state와 맞지 않는 경우 (예: 없는 target_id)."""


# 분류 결과를 기존 상태의 복사본에 병합하고 항목 ID와 정정 이력을 관리한다.
# 아직 DB에는 저장하지 않으므로, 뒤의 응답 생성이 실패하면 이 변경은 버려진다.
def merge(state, result: Classification, turn, next_id, user_message=None):
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
        if item.get("source") == "rapport_card":
            # 카드를 고른 사실과 달리, 이번에는 사용자가 대화로 항목을 다루었으므로 실제 근거를 남긴다.
            item["source"] = "conversation"
            item["evidence"] = u.evidence
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
        if not text or any(item["text"] == text for item in conversation_items(state, key)):
            continue  # 같은 내용이 이미 있으면 다시 추가하지 않는다
        state[key].append({
            "id": next_id,
            "text": text,
            "evidence": n.evidence,
            "turn": turn,
            "status": n.status,
        })
        next_id += 1

    exploration.update(state, result, turn, user_message)
    return state, next_id


# --- 응답 생성 프롬프트용 요약 ---

_LABELS = {"experiences": "경험·관심 활동", "criteria": "선택 기준", "constraints": "현실적 제약"}


# 분류에 필요한 현재 항목·질문·탐색 방향만 전달한다. 정정 원본과 내부 카운터는 DB 상태에 보관한다.
def classification_state(state):
    stage = ensure_flow(state)
    rapport = state["rapport"]
    controller = state["controller"]
    return {
        "preferred_name": state["preferred_name"],
        "conversation_stage": stage,
        "exploration": exploration.classification_context(state),
        **{key: [{field: item[field] for field in ("id", "text", "status")}
                 for item in conversation_items(state, key)] for key in _LABELS},
        "rapport": {
            "category": rapport["category"], "done": rapport["done"],
            "questions": [{field: q[field] for field in ("id", "text", "status")}
                          for q in rapport["questions"]] if stage == "rapport" else [],
        },
        "controller": {
            "direction": {key: controller["direction"][key] for key in ("label", "awaiting_choice")},
            "last_question": controller["last_question"],
        },
    }


# 확인 상태별로 사용자 정보를 요약하고, 컨트롤러가 고른 이번 턴의 계획을 응답 모델에 전달한다.
def format_state(state, turn=None, plan=None):
    """응답 호출의 시스템 프롬프트에 붙일 현재 상태 요약. withdrawn 항목은 활성 목록에서 제외한다.
    plan(plan_turn 결과)을 주면 '서로 알아가기' 진행과 '이번 턴의 질문' 지시를 함께 붙인다."""
    ensure_flow(state)
    lines = []
    if plan is not None:
        rapport_text = format_rapport(state["rapport"], plan["rapport_action"])
        if rapport_text:
            lines.append(rapport_text)
        lines.append(format_turn_plan(plan))
    lines.append("## 현재까지 확인한 사용자 정보")
    lines.append(f"호칭: {state['preferred_name'] or '(아직 확인 안 됨)'}")
    for key, label in _LABELS.items():
        confirmed = [i for i in conversation_items(state, key) if i["status"] == "confirmed"]
        unconfirmed = [i for i in conversation_items(state, key) if i["status"] == "unconfirmed"]
        # 응답 모델이 basis_ids를 정확히 고를 수 있도록 항목 내용과 실제 id를 함께 전달한다.
        lines.append(f"[확인된 {label}] " + (
            "; ".join(f"id {i['id']}: {i['text']}" for i in confirmed) if confirmed else "없음"))
        lines.append(f"[미확인 {label}] " + (
            "; ".join(f"id {i['id']}: {i['text']}" for i in unconfirmed) if unconfirmed else "없음"))
    withdrawn = [i for i in conversation_items(state) if i["status"] == "withdrawn"]
    if withdrawn:
        lines.append("[철회한 정보 (withdrawn)] " + "; ".join(f"id {i['id']}: {i['text']}" for i in withdrawn))
    cards = state.get("rapport", {}).get("cards")
    if cards and cards.get("chosen_id"):
        chosen = next((c for c in cards["offered"] if c["id"] == cards["chosen_id"]), None)
        if chosen:
            lines.append(f"[라포에서 선택한 카드 — 배경정보] {chosen['name']}: {chosen['meaning']}")
    return "\n".join(lines)
