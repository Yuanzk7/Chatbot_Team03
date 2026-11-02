"""세 통제 규칙과 질문 선택을 담당한다. DB·API 호출 없이 상태와 분류 결과만 사용한다.

흐름: plan_turn(조건 판정) → LLM 응답 → finalize_reply(검증·조립)
      → complete_turn(실제로 전달한 질문 기록). 저장은 routes가 한 트랜잭션으로 처리한다.
"""
from dataclasses import dataclass, field

from counseling.flow import (
    INTRO_QUESTION, apply_input, conversation_items, ensure_flow, enter_career, suggestions_for_stage,
)
from counseling.rapport import choose_action

DIRECTION_LIMIT = 10
CAREER_CONCERN_QUESTION = "지금 진로와 관련해서 어떤 고민이 있으신가요?"


@dataclass
class ControlledReply:
    """화면에 전달할 답변과, 저장할 검증 결과를 함께 담는 내부 자료형."""
    content: str
    suggestions: list[str] = field(default_factory=list)
    asked_question_id: int | None = None
    rapport_cards: list = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    used_basis_ids: list[int] = field(default_factory=list)
    question: str | None = None


# 상담별 통제 설정과 진행 상태를 만든다. 대화의 turn 번호와 탐색 횟수는 별개다.
def empty_controller(enabled=True):
    return {
        "enabled": enabled,
        "direction": {"label": None, "turns": 0, "awaiting_choice": False,
                      "needs_clarification": False},
        "last_processed_turn": None,       # 같은 턴의 계획을 다시 계산해도 방향 횟수는 한 번만 증가
        "pending_confirmation_ids": [],    # 우선 질문 때문에 미룬 해석도 다음 턴에 유지
        "asked_confirmations": {},         # 항목 id → 이미 질문했거나 사용자가 유보한 내용의 버전
        "last_question": None,             # 다음 입력의 짧은 동의·유보가 무엇을 가리키는지 판단할 단서
        "pending_card_offer": None,        # 다른 질문 때문에 아직 보여주지 못한 wrap_up/concern 카드
    }


# 이전 버전 상담에도 새 필드를 채운다. 기존 설정·대화 기록은 덮어쓰지 않는다.
def ensure_controller(state, enabled=True):
    controller = state.setdefault("controller", {})
    for key, value in empty_controller(enabled).items():
        controller.setdefault(key, value)
    for key, value in empty_controller()["direction"].items():
        controller["direction"].setdefault(key, value)
    return controller


# 대화로 다룬 경험·기준·제약을 id로 합친다. 카드 자동 항목은 제외하고 철회 항목은 정정 확인에 남긴다.
def _items(state):
    return {i["id"]: i for i in conversation_items(state)}


# 같은 해석을 반복 확인하지 않도록 항목 내용과 마지막 정정 턴을 버전으로 사용한다.
def _item_version(state, item):
    changed = max([item["turn"]] + [c["turn"] for c in state["corrections"]
                                  if c["target_id"] == item["id"]])
    return f"{changed}:{item['text']}"


# 이번 턴에 새로 생기거나 정정된 미확인 항목을 찾는다. 기존 라포 계획에서도 사용한다.
def needs_confirmation(state, turn):
    changed_ids = {c["target_id"] for c in state["corrections"] if c["turn"] == turn}
    return [i["text"] for i in _items(state).values()
            if i["status"] == "unconfirmed" and (i["turn"] == turn or i["id"] in changed_ids)]


# LLM은 방향이 같은지 분류하고, 실제 카운터와 초기화는 이 함수가 처리한다.
# 선택 대기 중에는 횟수를 동결한다. 정보가 불명확하거나 방향과 무관한 대화도 증가시키지 않는다.
def _advance_direction(controller, result, turn):
    direction = controller["direction"]
    if controller["last_processed_turn"] == turn:
        return
    controller["last_processed_turn"] = turn
    direction["needs_clarification"] = False
    if direction["awaiting_choice"]:
        if result.direction_choice == "continue":
            direction.update(turns=0, awaiting_choice=False)
            return
        if result.direction_choice == "switch":
            direction.update(label=None, turns=0, awaiting_choice=False, needs_clarification=True)
            # 새 방향도 함께 명시했다면 아래에서 첫 탐색 턴으로 센다.
            if result.direction_relation != "new":
                return
        else:
            return
    label = (result.direction_label or "").strip()
    if result.direction_relation == "new" and label:
        direction.update(label=label, turns=1, needs_clarification=False)
    elif result.direction_relation == "same" and direction["label"]:
        direction["turns"] += 1
    elif result.direction_relation in ("unclear", "same", "new"):
        direction["needs_clarification"] = True
    if direction["turns"] >= DIRECTION_LIMIT:
        direction["awaiting_choice"] = True


# 새 해석·이전부터 남은 관련 해석을 확인 대기열에 넣는다. 사용자가 유보한 항목은 재촉하지 않는다.
def _queue_confirmations(state, result, turn):
    controller = state["controller"]
    items = _items(state)
    relevant = set(result.relevant_item_ids)
    relevant.update(c["target_id"] for c in state["corrections"] if c["turn"] == turn)
    relevant.update(i["id"] for i in items.values() if i["turn"] == turn)
    if result.request_type == "recommendation":
        relevant.update(items)
    for item_id in result.deferred_item_ids:
        item = items.get(item_id)
        if item and item["status"] == "unconfirmed":
            controller["asked_confirmations"][str(item_id)] = _item_version(state, item)
    pending = controller["pending_confirmation_ids"]
    for item_id in sorted(relevant):
        if item_id in items and item_id not in pending:
            pending.append(item_id)
    controller["pending_confirmation_ids"] = [
        item_id for item_id in pending if item_id in items
        and items[item_id]["status"] == "unconfirmed"
        and controller["asked_confirmations"].get(str(item_id)) != _item_version(state, items[item_id])
    ]


# 기존 라포·후속 질문 선택 로직이다. 통제를 끈 비교 상담에도 같은 대화 원칙을 사용한다.
# 상위 컨트롤러가 확인 대상을 골랐다면 check_confirmation=False로 중복 선택을 막는다.
def plan_rapport_turn(state, result, turn, current_rapport_id=None, check_confirmation=True):
    confirm_items = needs_confirmation(state, turn) if check_confirmation else []
    new_texts = [n.text for n in result.new_items] + [(u.text or "").strip() for u in result.updates if u.text]
    r = state["rapport"]
    answered_q = next((q for q in r["questions"]
                       if q["id"] == current_rapport_id and q["id"] in result.rapport_answered_ids), None)
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
    return {"question": question, "confirm_items": confirm_items, "new_texts": new_texts,
            "rapport_action": action}


# 우선 질문과 카드 제시가 겹치면 카드를 보류한다. 다음 일반 턴에 다시 제시할 수 있도록 기록한다.
def _hold_rapport(state, turn):
    action = choose_action(state["rapport"], turn, hold=True)
    if action["type"] in ("wrap_up", "concern"):
        state["controller"]["pending_card_offer"] = action["type"]
    return {"type": "none" if state["rapport"]["done"] else "hold", "question": None}


# 계획에 실제 대화 단계를 붙인다. 카드 제시는 일반 질문과 한 턴에 겹치지 않게 한다.
def _finish_plan(state, plan):
    action = plan["rapport_action"]["type"]
    if action == "wrap_up":
        state["conversation_stage"] = "cards"
        plan["question"] = "none"
    elif action == "concern":
        enter_career(state, plan["turn"])
        plan["rapport_action"] = {"type": "none", "question": None}
        plan["question"] = "reflect_after_answer" if state["career_concern_received"] else "career_concern"
        plan["fixed_question"] = None if state["career_concern_received"] else CAREER_CONCERN_QUESTION
    plan["conversation_stage"] = state["conversation_stage"]
    return plan


# 동의·중단·카드 선택으로 단계를 전환하고, 세 통제 규칙과 이번 질문 하나를 결정한다.
# 정보 요청의 답변 유형과 후속 질문 여부는 별개이며, 종료·설명만 요청하면 질문을 생략한다.
def plan_turn(state, result, turn, current_rapport_id=None, card=None, career_entry=False):
    """state는 아직 저장하지 않은 상담 상태, result는 LLM 분류, turn은 대화 쌍의 번호다.
    card/career_entry는 카드 UI에서 들어온 이벤트로, 탐색 횟수를 증가시키지 않는다.
    반환 계획의 제약은 답변 전체에 적용하고 question/fixed_question은 질문 하나를 지정한다.
    """
    controller = ensure_controller(state)
    before_stage = ensure_flow(state)
    enabled = controller["enabled"]
    if result:
        apply_input(state, result, turn)
    confirmed = {i["id"] for i in _items(state).values() if i["status"] == "confirmed"}
    experiences = [i["id"] for i in conversation_items(state, "experiences") if i["id"] in confirmed]
    criteria = [i["id"] for i in conversation_items(state, "criteria") if i["id"] in confirmed]
    plan = {"question": "none", "confirm_items": [], "confirm_item_ids": [], "new_texts": [],
            "rapport_action": {"type": "none", "question": None}, "turn": turn,
            "controller_enabled": enabled, "request_type": result.request_type if result else "reflection",
            "allow_recommendation": bool(experiences and criteria) if enabled else True,
            "allowed_basis_ids": sorted(confirmed), "experience_ids": experiences, "criterion_ids": criteria,
            "fixed_question": None, "triggered_rules": [], "card": card}
    if enabled and not plan["allow_recommendation"]:
        plan["triggered_rules"].append("recommendation_gate")
    if card or career_entry:
        enter_career(state, turn, reason="card_selected" if card else "cards_dismissed")
        plan["question"] = "reflect_after_answer" if state["career_concern_received"] else "career_concern"
        plan["fixed_question"] = None if state["career_concern_received"] else CAREER_CONCERN_QUESTION
        return _finish_plan(state, plan)
    stage = state["conversation_stage"]
    if result is None or result.follow_up == "close":
        return _finish_plan(state, plan)
    if stage == "intro":
        if result.follow_up != "answer_only":
            plan.update(question="intro_permission", fixed_question=INTRO_QUESTION)
        return _finish_plan(state, plan)
    if stage == "cards":
        # 카드에 대한 질문에는 제시를 유지한다. 실제 진로 고민을 말하면 apply_input이 상담으로 전환한다.
        return _finish_plan(state, plan)
    if enabled:
        if stage == "career":
            _advance_direction(controller, result, turn)
        _queue_confirmations(state, result, turn)
    if result.follow_up == "answer_only":
        if stage == "rapport":
            plan["rapport_action"] = _hold_rapport(state, turn)
        return _finish_plan(state, plan)
    if before_stage != "career" and stage == "career":
        plan["question"] = "reflect_after_answer" if state["career_concern_received"] else "career_concern"
        plan["fixed_question"] = None if state["career_concern_received"] else CAREER_CONCERN_QUESTION
        return _finish_plan(state, plan)
    direction = controller["direction"]
    pending = controller["pending_confirmation_ids"]
    if enabled and stage == "career" and direction["awaiting_choice"]:
        plan["triggered_rules"].append("direction_checkpoint")
        if result.direction_choice == "unsure":
            plan.update(question="direction_help", fixed_question="계속 살펴볼지 결정할 때 어떤 점이 가장 고민되나요?")
        else:
            plan.update(question="direction_check", fixed_question=
                        f"‘{direction['label']}’에 대해 10턴 동안 탐색했어요. 이 방향을 계속 탐색할까요, 아니면 다른 가능성을 살펴볼까요?")
    elif enabled and pending:
        item = _items(state)[pending[0]]
        plan["triggered_rules"].append("confirm_interpretation")
        plan.update(question="confirm", confirm_items=[item["text"]], confirm_item_ids=[item["id"]],
                    fixed_question=f"‘{item['text']}’에 대해 지금은 어떻게 생각하시나요?")
    elif enabled and stage == "career" and result.request_type == "recommendation" and not experiences:
        plan.update(question="gather_experience", fixed_question="지금까지 재미있었던 경험이나 해보고 싶은 활동은 무엇인가요?")
    elif enabled and stage == "career" and result.request_type == "recommendation" and not criteria:
        plan.update(question="gather_criterion", fixed_question="진로를 선택할 때 본인이 중요하게 생각하는 것은 무엇인가요?")
    elif enabled and result.needs_reason:
        plan["triggered_rules"].append("confirm_interpretation")
        plan.update(question="explore_reason", fixed_question="그 선택을 생각하게 된 이유나 경험은 무엇인가요?")
    elif enabled and stage == "career" and direction["needs_clarification"]:
        plan.update(question="clarify_direction", fixed_question="어떤 분야나 활동을 중심으로 더 탐색해 보고 싶으신가요?")
    elif stage == "career":
        plan["question"] = "reflect_after_answer"
    else:
        if controller["pending_card_offer"]:
            plan["rapport_action"] = {"type": controller["pending_card_offer"], "question": None}
        else:
            plan.update(plan_rapport_turn(state, result, turn, current_rapport_id, check_confirmation=False))
        return _finish_plan(state, plan)
    if stage == "rapport":
        plan["rapport_action"] = _hold_rapport(state, turn)
    return _finish_plan(state, plan)


# 응답 모델에 허용된 근거·질문·제한을 전달한다. 내부 판정 정보는 사용자에게 보여주지 않는다.
def format_turn_plan(plan):
    lines = ["## 이번 턴의 질문 (하나만)"]
    if plan.get("controller_enabled"):
        lines.extend([
            "개인화된 설명의 근거는 confirmed 항목만 사용한다. allowed_basis_ids=" + str(plan["allowed_basis_ids"]),
            f"개인화된 후보 추천 허용={plan['allow_recommendation']}. 요청 유형={plan['request_type']}.",
            "추천 허용이 false면 recommendations는 빈 배열이다. 일반 정보 요청에는 사실 설명으로 답한다.",
            "개인화된 추천은 사용자 요청이 recommendation일 때만 recommendations에 따로 쓴다.",
            "content에는 추천이나 미확인 동기·적성 판단을 섞지 않는다. 사회적 통념으로 이유를 추측하지 않는다.",
        ])
    if plan.get("conversation_stage") in ("rapport", "cards"):
        lines.append("이 단계는 자유응답 라포·카드 단계다. 예시 답변을 유도하지 말고 suggestions는 빈 배열로 둔다.")
    if plan["question"] == "career_concern":
        if plan.get("card"):
            lines.append("카드 선택을 짧게 받아들이되, 선택 이유나 과거 경험을 추가로 묻지 않는다.")
        lines.append(
            "이제 본격적으로 진로 이야기를 듣는 전환이다. 알아가기나 카드 선택을 요구하지 않는다. "
            "고르지 않은 카드를 골랐다고 말하지 않는다. 사용자가 현재 가진 진로 고민을 먼저 듣는다."
        )
    if plan.get("fixed_question"):
        lines.append("질문은 코드가 다음 문장으로 붙인다. content에는 질문 없이 짧게 답하고 question은 null로 둔다: "
                     + plan["fixed_question"])
    elif plan["question"] == "confirm":
        lines.append("다음 해석을 확인하는 질문 하나로 한다: " + "; ".join(plan["confirm_items"]))
    elif plan["question"] == "follow_up":
        lines.append("새 이야기(" + "; ".join(plan["new_texts"]) + ")를 구체화하는 후속 질문 하나만 question에 쓴다.")
    elif plan["question"] == "reflect_after_answer":
        lines.append("사용자가 요청한 정보·비교에는 먼저 2~3문장으로 명확하게 답한다. 그다음 현재 고민·경험을 구체화하는 열린 질문 하나를 question에 쓴다. 이미 말한 고민을 처음부터 다시 묻거나 카드에 억지로 연결하지 않는다.")
    elif plan["question"] == "none":
        lines.append("추가 질문 없이 사용자의 요청에 답하거나 카드 선택을 안내한다. question은 null이다.")
    else:
        lines.append("위 '서로 알아가기' 지시를 따른다. 라포가 끝났으면 탐색 질문 하나를 question에 쓴다.")
    return "\n".join(lines)


# 근거 ID와 추천 조건을 검사한다. 위반하면 모델을 재호출하지 않고 안전한 질문 응답으로 바꾼다.
def finalize_reply(state, plan, reply):
    """구조화 필드로 선언된 추천·근거를 검사한다. 자유 문장의 의미 자체를 판정하는 함수는 아니다.
    본문에 숨긴 단정 등은 프롬프트와 실제 대화 시나리오 평가로 별도 확인해야 한다.
    """
    errors = []
    content = reply.content.strip()
    recommendations = reply.recommendations
    used_ids = set(reply.basis_ids)
    if plan["controller_enabled"]:
        allowed = set(plan["allowed_basis_ids"])
        if not used_ids <= allowed:
            errors.append("unconfirmed_or_unknown_basis")
        if "?" in content or "？" in content:
            errors.append("question_in_content")
        if recommendations and (not plan["allow_recommendation"] or plan["request_type"] != "recommendation"):
            errors.append("recommendation_not_allowed")
        for candidate in recommendations:
            basis = set(candidate.basis_ids)
            if (not basis <= allowed or not basis.intersection(plan["experience_ids"])
                    or not basis.intersection(plan["criterion_ids"])):
                errors.append("recommendation_missing_confirmed_basis")
            if "?" in candidate.reason or "？" in candidate.reason:
                errors.append("question_in_recommendation")
    if any(not c.candidate.strip() or not c.reason.strip() for c in recommendations):
        errors.append("empty_recommendation")
    question = plan.get("fixed_question") or (reply.question or "").strip() or None
    if plan["question"] == "none":
        question = None
    if plan["controller_enabled"] and plan["question"] != "none" and not question:
        errors.append("missing_question")
    if question and (question.count("?") + question.count("？") > 1):
        errors.append("multiple_questions")
    if not content and not question and not recommendations:
        errors.append("empty_reply")
    fallback_rapport_id = None
    if errors == ["missing_question"]:
        # 본문·추천 근거가 유효하고 질문만 빠졌다면 설명을 버리지 않고 질문만 보완한다.
        action_q = plan["rapport_action"].get("question")
        question = action_q["text"] if action_q else "지금 이야기에서 가장 고민되는 점은 무엇인가요?"
        fallback_rapport_id = action_q["id"] if action_q else None
    elif errors:
        content = "말씀하신 내용부터 차근차근 살펴볼게요."
        question = plan.get("fixed_question")
        if plan["question"] != "none" and not question:
            action_q = plan["rapport_action"].get("question")
            question = (action_q["text"] if action_q else "지금 이야기에서 가장 중요하게 느끼는 점은 무엇인가요?")
            # 코드가 원래 라포 질문을 직접 전달했다면 실제로 물은 질문으로 기록한다.
            fallback_rapport_id = action_q["id"] if action_q else None
        elif plan["rapport_action"]["type"] in ("wrap_up", "concern"):
            content = "아래 카드 중 지금 가장 나다운 카드 하나를 골라 주세요."
        elif plan["question"] == "none":
            content = "궁금한 내용을 조금 더 구체적으로 말씀해 주세요."
        recommendations, used_ids = [], set()
    for candidate in recommendations:
        content += f"\n\n**{candidate.candidate.strip()}**: {candidate.reason.strip()}"
        used_ids.update(candidate.basis_ids)
    if question:
        content = (content + "\n\n" + question).strip()
    suggestions = []
    if question:
        for suggestion in reply.suggestions if not errors else []:
            suggestion = suggestion.strip()
            if suggestion and len(suggestion) <= 30 and suggestion not in suggestions:
                suggestions.append(suggestion)
    if plan["question"] == "direction_check":
        suggestions = ["이 방향으로 더 탐색할게요", "다른 가능성을 살펴볼게요", "아직 잘 모르겠어요"]
    action = plan["rapport_action"]
    asked_id = fallback_rapport_id
    if not errors and question and action["type"] in ("ask", "continue") and action.get("question"):
        if reply.asked_question_id == action["question"]["id"]:
            asked_id = reply.asked_question_id
    picks = reply.rapport_cards if not errors and action["type"] == "wrap_up" else []
    suggestions = suggestions_for_stage(state, suggestions, has_question=bool(question))
    return ControlledReply(content, suggestions[:3], asked_id, picks, sorted(set(errors)), sorted(used_ids), question)


# 실제로 전달한 질문만 기록한다. 이후 DB 저장이 실패하면 이 변경도 저장되지 않는다.
def complete_turn(state, plan, reply):
    controller = ensure_controller(state)
    controller["last_question"] = None
    if reply.question:
        controller["last_question"] = {"type": plan["question"], "item_ids": plan.get("confirm_item_ids", []),
                                       "text": reply.question}
        for item_id in plan.get("confirm_item_ids", []):
            controller["asked_confirmations"][str(item_id)] = _item_version(state, _items(state)[item_id])
            if item_id in controller["pending_confirmation_ids"]:
                controller["pending_confirmation_ids"].remove(item_id)
    if plan["rapport_action"]["type"] in ("wrap_up", "concern"):
        controller["pending_card_offer"] = None


# 턴별 판정을 JSON으로 묶어 어떤 규칙이 작동했고 어떤 응답을 보냈는지 비교할 수 있게 한다.
def decision_record(state, plan, reply):
    return {"enabled": plan["controller_enabled"], "request_type": plan["request_type"],
            "conversation_stage": state["conversation_stage"],
            "triggered_rules": plan["triggered_rules"], "question": plan["question"],
            "allow_recommendation": plan["allow_recommendation"], "direction": dict(state["controller"]["direction"]),
            "confirm_item_ids": plan.get("confirm_item_ids", []), "used_basis_ids": reply.used_basis_ids,
            "validation_errors": reply.validation_errors}
