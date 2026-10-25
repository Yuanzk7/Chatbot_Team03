"""진로맵(career_map) 스키마와 조립 로직. MVP_Design.md 3.4 참고."""
from datetime import datetime, timezone
from typing import List, Literal

from pydantic import BaseModel


# --- 진로맵 호출 출력 스키마. 선택 기준은 state에서 코드가 채우므로 모델은 후보·행동만 돌려준다 ---

class CriterionNote(BaseModel):
    criterion_id: int
    note: str


class Candidate(BaseModel):
    name: str
    source: Literal["user", "assistant"]
    by_criteria: List[CriterionNote]


class NextAction(BaseModel):
    text: str
    selected: bool


class CareerMapOutput(BaseModel):
    candidates: List[Candidate]
    next_actions: List[NextAction]


class CareerMapError(Exception):
    """모델 출력이 현재 state와 맞지 않는 경우 (예: 없는 criterion_id)."""


def active_criteria(state):
    """withdrawn을 제외한 선택 기준. 진로맵과 프롬프트에 함께 쓴다."""
    return [
        {"id": i["id"], "text": i["text"], "status": i["status"]}
        for i in state["criteria"]
        if i["status"] != "withdrawn"
    ]


def build_career_map(state, output: CareerMapOutput, last_message_id):
    criteria = active_criteria(state)
    known_ids = {c["id"] for c in criteria}

    candidates = []
    for cand in output.candidates:
        name = cand.name.strip()
        if not name:
            raise CareerMapError("후보 이름이 비어 있습니다")
        notes, seen = [], set()
        for n in cand.by_criteria:
            if n.criterion_id not in known_ids:
                raise CareerMapError(f"존재하지 않는 기준 id: {n.criterion_id}")
            note = n.note.strip()
            # 공백 설명은 생략과 같다 (화면에 '추가 확인 필요'로 표시). 같은 기준이 반복되면 첫 번째만 쓴다
            if not note or n.criterion_id in seen:
                continue
            seen.add(n.criterion_id)
            notes.append({"criterion_id": n.criterion_id, "note": note})
        candidates.append({"name": name, "source": cand.source, "by_criteria": notes})

    next_actions = []
    for a in output.next_actions:
        text = a.text.strip()
        if not text:
            raise CareerMapError("행동 문장이 비어 있습니다")
        next_actions.append({"text": text, "selected": a.selected})

    return {
        "criteria": criteria,
        "candidates": candidates,
        "next_actions": next_actions,
        "based_on_message_id": last_message_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
