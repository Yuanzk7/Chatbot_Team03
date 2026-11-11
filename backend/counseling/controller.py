"""세 통제 규칙과 질문 선택을 담당한다. DB·API 호출 없이 상태와 분류 결과만 사용한다.

흐름: plan_turn(조건 판정) → LLM 응답 → finalize_reply(검증·조립)
      → complete_turn(실제로 전달한 질문 기록). 저장은 routes가 한 트랜잭션으로 처리한다.
"""
import re
from dataclasses import dataclass, field

from counseling import exploration
from counseling.flow import (
    INTRO_QUESTION, apply_input, conversation_items, ensure_flow, enter_career, suggestions_for_stage,
)
from counseling.rapport import choose_action

DIRECTION_LIMIT = 10
CAREER_CONCERN_QUESTION = "지금 진로와 관련해서 어떤 고민이 있으신가요?"
RECOMMENDATION_FALLBACK = "지금까지 확인한 경험과 기준만으로는 진로 후보를 추천하기 어려워요."
RETRY_CONTENT = "답변을 제대로 만들지 못했어요. 같은 내용을 한 번 더 보내 주시면 다시 답할게요."
# 알아가기 중 필수 질문이 빠졌을 때 붙이는 질문. 진로 고민을 묻는 일반 질문은 알아가기에 맞지 않는다.
RAPPORT_FALLBACK_QUESTION = "그 이야기를 조금 더 들려주실 수 있나요?"
RECOMMENDATION_ERRORS = {"recommendation_not_allowed", "recommendation_missing_confirmed_basis"}
QUESTION_MARKS = ("?", "？")
_QUOTED = re.compile(r"‘[^’\n]*’|“[^”\n]*”|'[^'\n]*'|\"[^\"\n]*\"|「[^」\n]*」|『[^』\n]*』")
_SENTENCE_END = re.compile(r"[.!?](?=\s)|[。！？\n]")  # 3.5 같은 소수점은 문장 끝이 아니다


@dataclass
class ControlledReply:
    """화면에 전달할 답변과, 저장할 검증 결과를 함께 담는 내부 자료형.
    validation_errors는 대체 응답으로 바꾼 차단 오류, warnings는 고쳐서 전달한 형식 문제다."""
    content: str
    suggestions: list[str] = field(default_factory=list)
    asked_question_id: int | None = None
    rapport_cards: list = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    used_basis_ids: list[int] = field(default_factory=list)
    question: str | None = None
    exploration: dict | None = None
    warnings: list[str] = field(default_factory=list)
    discarded: dict | None = None


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


# 경험·기준·제약 중 무엇을 해석했는지 밝힌다. 막연한 의견 대신 해석이 맞는지 확인한다.
def _confirmation_question(state, item):
    if any(i["id"] == item["id"] for i in conversation_items(state, "criteria")):
        return f"진로를 고를 때 ‘{item['text']}’도 중요하게 생각하신다고 이해했는데, 맞나요?"
    if any(i["id"] == item["id"] for i in conversation_items(state, "constraints")):
        return f"진로 선택에서 고려할 조건이 ‘{item['text']}’라고 이해했는데, 맞나요?"
    return f"경험이나 관심 활동을 ‘{item['text']}’로 이해했는데, 맞나요?"


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
    if action["type"] == "wrap_up":
        state["controller"]["pending_card_offer"] = action["type"]
    return {"type": "none" if state["rapport"]["done"] else "hold", "question": None}


# 계획에 실제 대화 단계를 붙인다. 카드 제시는 일반 질문과 한 턴에 겹치지 않게 한다.
def _finish_plan(state, plan):
    action = plan["rapport_action"]["type"]
    if action == "wrap_up":
        state["conversation_stage"] = "cards"
        plan["question"] = "none"
    plan["conversation_stage"] = state["conversation_stage"]
    plan["exploration"] = exploration.plan_turn(state, plan)
    return plan


# 동의·카드 확인으로 단계를 전환하고, 세 통제 규칙과 이번 질문 하나를 결정한다.
# 일반 상담의 후속 질문은 선택이다. 라포·해석 확인·방향 점검 등 필요한 질문만 필수로 지정한다.
def plan_turn(state, result, turn, current_rapport_id=None, card=None):
    """state는 아직 저장하지 않은 상담 상태, result는 LLM 분류, turn은 대화 쌍의 번호다.
    card는 카드를 확인하고 진로 상담을 시작하는 UI 이벤트로, 탐색 횟수를 증가시키지 않는다.
    반환 계획의 제약은 답변 전체에 적용하고 question/fixed_question은 질문 하나를 지정한다.
    """
    controller = ensure_controller(state)
    ensure_flow(state)
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
            "fixed_question": None, "triggered_rules": [], "card": card,
            "concern_now": bool(result and result.concern_provided)}
    plan["follow_up"] = result.follow_up if result else "reflect"
    if enabled and not plan["allow_recommendation"]:
        plan["triggered_rules"].append("recommendation_gate")
    if card:
        enter_career(state, turn)
        plan["question"] = "optional" if state["career_concern_received"] else "career_concern"
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
        # 카드 단계에서는 상담으로 넘어가지 않는다. 진로 상담은 카드를 확인하는 버튼으로만 시작한다.
        return _finish_plan(state, plan)
    if enabled:
        if stage == "career":
            _advance_direction(controller, result, turn)
        _queue_confirmations(state, result, turn)
    # 알아가기는 중단할 수 없으므로, 설명만 원해도 라포 질문은 이어간다.
    if result.follow_up == "answer_only" and stage != "rapport":
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
                    fixed_question=_confirmation_question(state, item))
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
        plan["question"] = "optional"
    else:
        if controller["pending_card_offer"]:
            plan["rapport_action"] = {"type": controller["pending_card_offer"], "question": None}
        else:
            plan.update(plan_rapport_turn(state, result, turn, current_rapport_id, check_confirmation=False))
        return _finish_plan(state, plan)
    if stage == "rapport":
        plan["rapport_action"] = _hold_rapport(state, turn)
    return _finish_plan(state, plan)


# 응답 모델에는 이번 단계·허용 근거·질문 방식만 전달한다. 공통 대화 원칙은 prompts.py에 둔다.
def format_turn_plan(plan):
    lines = ["## 이번 턴의 질문", f"대화 단계: {plan.get('conversation_stage', 'rapport')}"]
    if plan.get("conversation_stage") == "rapport":
        # 알아가기 마무리는 코드가 정한다. 진로 이야기를 미룬다는 안내는 이번에 실제로 고민·요청을 말한 턴에만 한다.
        line = "알아가기 단계다. 알아가기를 끝낸다는 말은 하지 않는다."
        if plan["request_type"] != "reflection" or plan.get("concern_now"):
            line += (" 이번 요청이나 진로 고민에 대한 설명·조언은 하지 않고, 알아가기를 마친 뒤 진로 상담에서 다룬다고 "
                     "한 문장으로 안내한다.")
        lines.append(line)
    if plan.get("controller_enabled"):
        lines.extend([
            f"요청 유형={plan['request_type']}, 개인화된 추천 허용={plan['allow_recommendation']}.",
            f"허용 근거 id={plan['allowed_basis_ids']}, 경험 id={plan['experience_ids']}, 기준 id={plan['criterion_ids']}.",
        ])
        if not plan["allow_recommendation"]:
            lines.append("사용자가 직접 연결하지 않은 경험을 특정 분야·전공·직무 방향과 연결하지 않는다. 분야는 일반 정보로만 설명한다.")
    if plan.get("conversation_stage") in ("intro", "rapport", "cards"):
        lines.append("suggestions는 빈 배열이다. 시작 안내의 두 선택지는 코드가 붙인다.")
    if plan.get("card") and plan["question"] == "career_concern":
        lines.append("진로 상담 화면이 새로 시작된다. 카드를 다시 해석하지 않고 현재 진로 고민을 듣는다.")
    if plan.get("fixed_question"):
        lines.append("질문은 코드가 붙인다. 먼저 요청에 답하고 question은 null: " + plan["fixed_question"])
    elif plan["question"] == "confirm":
        lines.append("다음 해석을 확인하는 질문 하나로 한다: " + "; ".join(plan["confirm_items"]))
    elif plan["question"] == "follow_up":
        lines.append("새 이야기(" + "; ".join(plan["new_texts"]) + ")를 구체화하는 후속 질문 하나만 question에 쓴다.")
    elif plan["question"] == "optional":
        lines.append("질문은 선택이다. 설명으로 충분하면 question=null. 답에 따라 진로 판단이나 다음 탐색이 달라질 때만 하나 묻는다.")
    elif plan["question"] == "none":
        lines.append("추가 질문 없이 답한다. question=null.")
    else:
        lines.append("지정된 라포 질문을 따른다.")
    if context := exploration.format_plan(plan.get("exploration")):
        lines.append(context)
    return "\n".join(lines)


# 인용 구간을 같은 길이의 공백으로 가린다. 설명 속 연구 질문 인용은 사용자에게 묻는 질문이 아니다.
def _mask_quotes(text):
    return _QUOTED.sub(lambda m: " " * len(m.group()), text)


# 인용을 제외하고 물음표가 남아 있는지 확인한다.
def _has_question(text):
    masked = _mask_quotes(text)
    return any(mark in masked for mark in QUESTION_MARKS)


# 끝 문장이 사용자에게 답을 요구하는 질문이면 (본문, 질문)으로 나눈다. 인용과 중간의 물음표는 본문에 남긴다.
def _split_trailing_question(content):
    masked = _mask_quotes(content).rstrip()
    if not masked.endswith(QUESTION_MARKS):
        return content, None
    end = len(masked.rstrip("?？"))
    start = max((m.end() for m in _SENTENCE_END.finditer(masked, 0, end)), default=0)
    return content[:start].rstrip(), content[start:].strip()


# 질문 필드에 질문이 여러 개면 첫 질문까지만 남기고, 나머지를 (첫 질문, 나머지)로 돌려준다.
def _split_extra_questions(question):
    masked = _mask_quotes(question)
    marks = [masked.find(mark) for mark in QUESTION_MARKS if mark in masked]
    if not marks:
        return question, None
    end = min(marks) + 1
    while end < len(masked) and masked[end] in "?？":
        end += 1
    rest = question[end:].strip()
    return (question[:end].strip(), rest) if _has_question(rest) else (question, None)


# 근거 ID·추천 조건 위반은 차단해 대체 응답으로 바꾸고, 질문 위치·개수 같은 형식 문제는 설명을 살린 채 고친다.
def finalize_reply(state, plan, reply):
    """구조화 필드로 선언된 추천·근거를 검사한다. 자유 문장의 의미 자체를 판정하는 함수는 아니다.
    본문에 숨긴 단정 등은 프롬프트와 실제 대화 시나리오 평가로 별도 확인해야 한다.
    모델을 다시 호출하지 않으며, 버리거나 떼어 낸 내용은 discarded에 남겨 판정 로그로 확인한다.
    """
    errors, warnings, discarded = [], [], {}
    content = reply.content.strip()
    # 같은 질문만 두 필드에 중복한 경우는 조립 단계에서 한 번만 표시한다. 다른 본문 질문은 계속 검사한다.
    if content == (reply.question or "").strip():
        content = ""
    recommendations = reply.recommendations
    used_ids = set(reply.basis_ids)
    if plan["controller_enabled"]:
        allowed = set(plan["allowed_basis_ids"])
        if not used_ids <= allowed:
            errors.append("unconfirmed_or_unknown_basis")
        if recommendations and (not plan["allow_recommendation"] or plan["request_type"] != "recommendation"):
            errors.append("recommendation_not_allowed")
        for candidate in recommendations:
            basis = set(candidate.basis_ids)
            if (not basis <= allowed or not basis.intersection(plan["experience_ids"])
                    or not basis.intersection(plan["criterion_ids"])):
                errors.append("recommendation_missing_confirmed_basis")
            if _has_question(candidate.reason):
                errors.append("question_in_recommendation")
    if any(not c.candidate.strip() or not c.reason.strip() for c in recommendations):
        errors.append("empty_recommendation")
    question = plan.get("fixed_question") or (reply.question or "").strip() or None
    requires_question = plan["question"] not in ("none", "optional")
    may_ask = plan["question"] != "none" and not (
        plan.get("exploration") and not plan["exploration"]["allow_question"])
    if not may_ask:
        question = None
    if plan["controller_enabled"]:
        # 사용자에게 묻는 끝 문장은 질문 자리가 비어 있으면 question으로 옮기고, 아니면 떼어 낸다.
        content, trailing = _split_trailing_question(content)
        if trailing:
            warnings.append("question_in_content")
            if question is None and may_ask:
                question = trailing
            else:
                discarded["content_question"] = trailing
        elif _has_question(content):
            warnings.append("question_in_content")  # 소제목·수사적 질문은 설명과 함께 남긴다
    fallback_rapport_id = None
    if plan["controller_enabled"] and requires_question and not question:
        # 필수 질문만 빠졌다면 설명을 버리지 않고 질문만 보완한다.
        warnings.append("missing_question")
        action_q = plan["rapport_action"].get("question")
        question = action_q["text"] if action_q else (
            RAPPORT_FALLBACK_QUESTION if plan.get("conversation_stage") == "rapport"
            else "지금 이야기에서 가장 고민되는 점은 무엇인가요?")
        fallback_rapport_id = action_q["id"] if action_q else None
    if question:
        question, extra = _split_extra_questions(question)
        if extra:
            warnings.append("multiple_questions")
            discarded["extra_questions"] = extra
    metadata = None
    if not errors:
        content, metadata = exploration.prepare_reply(plan.get("exploration"), reply, content)
    if not content and not question and not recommendations:
        errors.append("empty_reply")
    if errors:
        # 사용자에게는 보내지 않지만 무엇을 막았는지 평가할 수 있도록 원문을 보관한다.
        discarded["reply"] = {
            "content": reply.content, "question": reply.question, "basis_ids": list(reply.basis_ids),
            "recommendations": [{"candidate": c.candidate, "reason": c.reason, "basis_ids": list(c.basis_ids)}
                                for c in reply.recommendations],
        }
        metadata, fallback_rapport_id = None, None
        question = plan.get("fixed_question")
        if requires_question and not question:
            action_q = plan["rapport_action"].get("question")
            question = action_q["text"] if action_q else (
                RAPPORT_FALLBACK_QUESTION if plan.get("conversation_stage") == "rapport"
                else "지금 이야기에서 가장 중요하게 느끼는 점은 무엇인가요?")
            # 코드가 원래 라포 질문을 직접 전달했다면 실제로 물은 질문으로 기록한다.
            fallback_rapport_id = action_q["id"] if action_q else None
        if plan["rapport_action"]["type"] == "wrap_up":
            content = "대화를 보고 고른 카드를 아래에 보여드릴게요."
        elif plan["request_type"] == "recommendation" and RECOMMENDATION_ERRORS.intersection(errors):
            content = RECOMMENDATION_FALLBACK
        elif question:
            content = ""  # 예고 문구 없이 필요한 질문만 전달한다
        else:
            content = RETRY_CONTENT
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
    return ControlledReply(content, suggestions[:3], asked_id, picks, sorted(set(errors)), sorted(used_ids), question,
                           metadata, sorted(set(warnings)), discarded or None)


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
    if plan["rapport_action"]["type"] == "wrap_up":
        controller["pending_card_offer"] = None
    exploration.complete_turn(state, plan, reply)


# 턴별 판정을 JSON으로 묶어 어떤 규칙이 작동했고 어떤 응답을 보냈는지 비교할 수 있게 한다.
def decision_record(state, plan, reply):
    return {"enabled": plan["controller_enabled"], "request_type": plan["request_type"],
            "conversation_stage": state["conversation_stage"],
            "triggered_rules": plan["triggered_rules"], "question": plan["question"],
            "allow_recommendation": plan["allow_recommendation"], "direction": dict(state["controller"]["direction"]),
            "confirm_item_ids": plan.get("confirm_item_ids", []), "used_basis_ids": reply.used_basis_ids,
            "validation_errors": reply.validation_errors, "warnings": reply.warnings,
            "discarded": reply.discarded,
            "exploration_focus": state.get("exploration", {}).get("focus")}
