"""진로 상담의 기준 → 조건 → 비교 → 행동 진행을 관리한다. DB와 모델 호출은 하지 않는다.

update는 사용자 발언을 반영하고, plan은 다음 목적을 판단한다.
complete는 검증을 통과해 실제 전달한 비교·행동·질문만 기록한다.
"""
from typing import Literal

from pydantic import BaseModel, Field

from counseling.flow import conversation_items

Focus = Literal["criteria", "conditions", "comparison", "action"]
LABELS = {"criteria": "기준 구체화", "conditions": "현실 조건 확인", "comparison": "후보 비교", "action": "다음 행동"}
ACKS = {"네", "응", "좋아", "좋아요", "맞아", "맞아요", "그래", "그래요", "네좋아요"}


class CandidateChange(BaseModel):
    name: str
    withdrawn: bool
    evidence: str


class ActionChange(BaseModel):
    action_id: int | None
    text: str | None
    status: Literal["selected", "withdrawn"]
    evidence: str


class ExplorationInput(BaseModel):
    concern: str | None
    focus: Focus | None
    conditions: Literal["unchanged", "needed", "not_needed"]
    defer: Focus | None
    candidates: list[CandidateChange] = Field(default_factory=list)
    actions: list[ActionChange] = Field(default_factory=list)


class ExplorationOutput(BaseModel):
    compared_candidates: list[str] = Field(default_factory=list)
    proposed_actions: list[str] = Field(default_factory=list)


# 사용자 정보는 기존 항목을 참조하고, 이곳에는 진행과 후보·행동만 저장한다.
def empty_exploration():
    return {"concern": None, "focus": "criteria", "requested_focus": None,
            "conditions_status": "unknown", "deferred_topics": [], "asked_topics": {},
            "candidates": [], "actions": [], "comparison": None, "last_proposed_ids": [],
            "relevant_item_ids": None}


# 이전 상담의 누락 필드만 보완한다. 이전 진로맵의 행동도 원래 선택 상태로 복원할 수 있다.
def ensure_exploration(state, legacy_actions=()):
    exploration = state.setdefault("exploration", {})
    for key, value in empty_exploration().items():
        exploration.setdefault(key, value)
    if legacy_actions and not exploration["actions"]:
        for action in legacy_actions:
            _record_action(exploration, action["text"], 0,
                           "selected" if action["selected"] else "proposed", "이전 진로맵")
    return exploration


# 띄어쓰기·대소문자 차이만 정리한다. 서로 다른 의미의 후보나 행동을 임의로 합치지 않는다.
def _key(text):
    return "".join(text.split()).casefold()


# 현재 방향에서 유효한 후보·행동만 반환한다. 방향 변경 전 기록은 원본 목록에 남는다.
def _active(exploration, section):
    return [entry for entry in exploration[section]
            if entry.get("active", True) and entry.get("status") != "withdrawn"]


# 새 탐색 방향에서는 해당 고민에 연결된 기존 항목 ID만 참조한다. 처음 상담과 이전 기록은 전체를 활용한다.
def _items(state, section=None):
    ids = state["exploration"]["relevant_item_ids"]
    return [i for i in conversation_items(state, section) if ids is None or i["id"] in ids]


# 같은 이름의 후보는 출처를 보존하며 활성 상태만 바꾼다.
def _record_candidate(exploration, name, source, active=True):
    existing = next((c for c in exploration["candidates"] if _key(c["name"]) == _key(name)), None)
    if existing:
        existing["active"] = active
    elif active:
        exploration["candidates"].append(dict(name=name, source=source, active=True))


# 실제 전달한 제안이나 사용자 결정을 안정된 ID로 저장한다. 같은 행동의 기존 선택 상태는 보존한다.
def _record_action(exploration, text, turn, status="proposed", evidence=""):
    text = text.strip()
    if not text:
        return None
    existing = next((a for a in exploration["actions"]
                     if _key(a["text"]) == _key(text) and a.get("active", True)), None)
    if existing:
        if status != "proposed":
            existing.update(status=status, evidence=evidence, active=True)
        return existing
    action = dict(id=max((a["id"] for a in exploration["actions"]), default=0) + 1,
                  text=text, status=status, evidence=evidence, turn=turn, active=True)
    exploration["actions"].append(action)
    return action


# 직전 답변에서 행동 하나를 제안하며 질문했을 때만 짧은 동의의 대상을 특정한다.
def _confirmation_action(state):
    e = ensure_exploration(state)
    question = state.get("controller", {}).get("last_question")
    ids = e["last_proposed_ids"]
    if e["focus"] == "action" and question and question["type"] == "optional" and len(ids) == 1:
        return ids[0]
    return None


# 사용자 발언만 반영한다. 모델이 인용한 근거가 실제 입력에 없으면 해당 변경은 적용하지 않는다.
def update(state, result, turn, user_message=None):
    e = ensure_exploration(state)
    signals = getattr(result, "exploration", None)
    direction = state.get("controller", {}).get("direction", {})
    changed = (direction.get("awaiting_choice") and result.direction_choice == "switch") or (
        result.direction_relation == "new" and result.direction_label and direction.get("label")
        and result.direction_label != direction["label"])
    if changed:
        e.update(focus="criteria", requested_focus=None, conditions_status="unknown",
                 deferred_topics=[], asked_topics={}, comparison=None, last_proposed_ids=[], concern=None,
                 relevant_item_ids=[])
        for entry in e["candidates"] + e["actions"]:
            entry["active"] = False
    if e["relevant_item_ids"] is not None:
        current = {i["id"] for i in conversation_items(state)}
        relevant = set(result.relevant_item_ids) | {i["id"] for i in conversation_items(state) if i["turn"] == turn}
        relevant.update(u.target_id for u in result.updates)
        e["relevant_item_ids"] = sorted((set(e["relevant_item_ids"]) | relevant) & current)
    if signals is None:
        return
    if signals.concern:
        e["concern"] = signals.concern.strip()
    if signals.focus:
        e["requested_focus"] = signals.focus
        e["deferred_topics"] = [f for f in e["deferred_topics"] if f != signals.focus]
        if signals.focus == "conditions" and e["conditions_status"] == "deferred":
            e["conditions_status"] = "needed"
    if signals.conditions != "unchanged":
        e["conditions_status"] = signals.conditions
    if signals.defer:
        if signals.defer not in e["deferred_topics"]:
            e["deferred_topics"].append(signals.defer)
        if signals.defer == "conditions":
            e["conditions_status"] = "deferred"
        if e["requested_focus"] == signals.defer:
            e["requested_focus"] = None
    for candidate in signals.candidates:
        if not candidate.evidence or (user_message is not None and candidate.evidence not in user_message):
            continue
        name = candidate.name.strip()
        if not name:
            continue
        _record_candidate(e, name, "user", not candidate.withdrawn)
    for change in signals.actions:
        if not change.evidence or (user_message is not None and change.evidence not in user_message):
            continue
        action = next((a for a in e["actions"] if a["id"] == change.action_id), None)
        if change.action_id is not None and action is None:
            continue
        if "".join(c for c in _key(change.evidence) if c.isalnum()) in ACKS and (
                action is None or action["id"] != _confirmation_action(state)):
            continue
        if action:
            action.update(status=change.status, evidence=change.evidence, active=True)
        elif change.text and change.status == "selected":
            _record_action(e, change.text, turn, change.status, change.evidence)


# 정정과 철회가 기존 비교에 영향을 주는지 확인할 현재 근거의 스냅샷을 만든다.
def _basis(state):
    e = state["exploration"]
    return {"items": [[i["id"], i["text"], i["status"]]
                      for i in sorted(_items(state), key=lambda i: i["id"])],
            "candidates": [c["name"] for c in _active(e, "candidates")],
            "concern": e["concern"]}


# 분류기에 필요한 현재 진행·행동 ID만 보낸다. 과거 질문 장부와 비활성 기록은 전달하지 않는다.
def classification_context(state):
    e = ensure_exploration(state)
    return {"concern": e["concern"], "focus": e["focus"], "conditions": e["conditions_status"],
            "relevant_item_ids": e["relevant_item_ids"],
            "deferred_topics": e["deferred_topics"],
            "candidates": [c["name"] for c in _active(e, "candidates")],
            "actions": [{k: a[k] for k in ("id", "text", "status")}
                        for a in e["actions"] if a.get("active", True)],
            "action_confirmation_id": _confirmation_action(state)}


# 필요한 정보·명시적 요청·실제로 전달한 비교로 이번 목적을 고른다. 상위 통제 질문에는 개입하지 않는다.
def plan_turn(state, plan):
    if (state.get("conversation_stage") != "career" or plan["question"] not in ("optional", "none")
            or plan.get("follow_up") == "close"):
        return None
    e = ensure_exploration(state)
    confirmed_criteria = any(i["status"] == "confirmed" for i in _items(state, "criteria"))
    constraints = _items(state, "constraints")
    if any(i["status"] == "confirmed" for i in constraints) and e["conditions_status"] != "deferred":
        e["conditions_status"] = "known"
    elif e["conditions_status"] == "known":
        e["conditions_status"] = "unknown"
    basis = _basis(state)
    candidates = [c["name"] for c in _active(e, "candidates")]
    comparison = e["comparison"]
    compared = bool(comparison and comparison["basis"] == basis
                    and {_key(n) for n in candidates} == {_key(n) for n in comparison["candidates"]})
    available = {f: f not in e["deferred_topics"] and e["asked_topics"].get(f) != basis for f in LABELS}
    if e["requested_focus"]:
        focus = e["requested_focus"]
    elif not confirmed_criteria and available["criteria"]:
        focus = "criteria"
    elif e["conditions_status"] == "needed" and available["conditions"]:
        focus = "conditions"
    elif len(candidates) >= 2 and not compared and "comparison" not in e["deferred_topics"]:
        focus = "comparison"
    else:
        focus = "action"
    e["focus"] = focus
    return {"focus": focus, "concern": e["concern"], "candidates": candidates,
            "conditions": e["conditions_status"], "basis": basis,
            "relevant_item_ids": e["relevant_item_ids"],
            "actions": [{k: a[k] for k in ("id", "text", "status")}
                        for a in e["actions"] if a.get("active", True)],
            "allow_question": bool(e["requested_focus"] or available[focus]),
            "allow_actions": "action" not in e["deferred_topics"],
            "withdrawn_actions": [a["text"] for a in e["actions"]
                                  if a.get("active", True) and a["status"] == "withdrawn"],
            "information": plan["request_type"] == "information"}


# 응답에는 이번 목적만 짧게 알려준다. 정보를 요청한 턴에는 단계 질문으로 답을 미루지 않는다.
def format_plan(context):
    if not context:
        return ""
    lines = [f"## 상담 진행: {LABELS[context['focus']]}"]
    if context["concern"]:
        lines.append("현재 고민: " + context["concern"])
    if context["relevant_item_ids"] is not None:
        lines.append("이번 고민의 관련 항목 id: " + str(context["relevant_item_ids"]))
    if context["information"]:
        lines.append("이번에는 요청한 정보를 충분히 설명한다. 단계 질문을 끼워 넣거나 소주제를 다시 고르게 하지 않는다.")
    elif context["focus"] == "criteria":
        lines.append("판단에 필요한 기준을 구체화한다. 이미 확인한 기준·이유는 다시 묻지 않는다.")
    elif context["focus"] == "conditions":
        lines.append("실제 선택에 영향을 주는 조건만 확인한다. 말하지 않은 비용·지역 제약을 추측하지 않는다.")
    elif context["focus"] == "comparison":
        lines.append("선택지와 확인된 기준을 연결해 비교한다. 정보가 부족한 부분은 확인할 점으로 남긴다.")
    else:
        lines.append("부족한 정보를 얻을 작은 탐색 행동을 제안할 수 있다. 이미 정한 행동을 재선택시키거나 결정을 재촉하지 않는다.")
    if context["candidates"]:
        lines.append("현재 후보: " + ", ".join(context["candidates"]))
    if context["actions"]:
        lines.append("행동 기록: " + "; ".join(f"{a['id']} [{a['status']}] {a['text']}" for a in context["actions"]))
    if not context["allow_question"]:
        lines.append("같은 내용에 이미 질문했거나 사용자가 미뤘다. 이번에는 추가 질문 없이 정리한다.")
    if not context["allow_actions"]:
        lines.append("행동 선택은 사용자가 미뤘다. 새로운 행동 제안을 붙이지 않는다.")
    return "\n".join(lines)


# 실제 본문에서 확인되는 비교와 표시할 행동만 승인한다. 행동은 코드가 조립해 전달 여부를 보장한다.
def prepare_reply(context, model_reply, content):
    output = getattr(model_reply, "exploration", None)
    if not context:
        return content, None
    introduced = [c.candidate.strip() for c in model_reply.recommendations]
    if output is None:
        return content, {"introduced_candidates": introduced} if introduced else None
    names = list(dict.fromkeys(n.strip() for n in output.compared_candidates if n.strip() and n.strip() in content))
    withdrawn = {_key(a) for a in context["withdrawn_actions"]}
    actions = list(dict.fromkeys(a.strip() for a in output.proposed_actions
                                if a.strip() and "?" not in a and "？" not in a
                                and _key(a) not in withdrawn))[:3] if context["allow_actions"] else []
    missing = [a for a in actions if a not in content]
    if missing:
        content = (content + "\n\n해볼 수 있는 일:\n" + "\n".join("- " + a for a in missing)).strip()
    return content, {"introduced_candidates": introduced, "compared_candidates": names, "proposed_actions": actions}


# 검증된 응답이 실제 전달된 뒤에만 질문·비교·행동을 기록한다. DB 실패 시 라우트가 함께 취소한다.
def complete_turn(state, plan, reply):
    context = plan.get("exploration")
    if not context or reply.validation_errors:
        return
    e = ensure_exploration(state)
    e["requested_focus"] = None
    e["last_proposed_ids"] = []
    metadata = getattr(reply, "exploration", None) or {}
    for name in metadata.get("introduced_candidates", []):
        _record_candidate(e, name, "assistant")
    names = metadata.get("compared_candidates", [])
    if len(names) >= 2:
        for name in names:
            _record_candidate(e, name, "assistant")
        e["comparison"] = dict(candidates=names, basis=_basis(state), turn=plan["turn"])
    for text in metadata.get("proposed_actions", []):
        action = _record_action(e, text, plan["turn"])
        if action and action.get("active", True) and action["status"] == "proposed":
            e["last_proposed_ids"].append(action["id"])
    if reply.question and not context["information"]:
        e["asked_topics"][context["focus"]] = _basis(state)


# 진로맵의 선택 여부는 사용자 결정 기록으로 채운다. 모델이 새로 제안한 일을 선택한 것으로 만들지 않는다.
def map_actions(state, proposed_actions):
    e = ensure_exploration(state)
    rows = [{"text": a["text"], "selected": a["status"] == "selected"} for a in _active(e, "actions")]
    seen = {_key(a["text"]) for a in rows}
    withdrawn = {_key(a["text"]) for a in e["actions"] if a.get("active", True) and a["status"] == "withdrawn"}
    for action in proposed_actions:
        key = _key(action["text"])
        if key not in seen and key not in withdrawn:
            rows.append({"text": action["text"], "selected": False})
            seen.add(key)
    return rows


# 진로맵에서 실제 보여준 제안도 다음 사용자 발언에서 선택·철회할 수 있도록 기록한다.
def record_summary(state, actions, turn):
    e = ensure_exploration(state)
    e["last_proposed_ids"] = []
    for action in actions:
        _record_action(e, action["text"], turn)
