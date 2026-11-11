"""첫 인사의 알아가기 질문 → 자유응답 라포 → 챗봇이 정한 카드 → 진로 상담의 전환과 이전 상담 호환을 관리한다.

카드는 배경정보다. 진로 기준과 근거 계산에는 사용자가 대화로 확인한 항목만 사용한다.
이 모듈은 DB에 쓰지 않으며, 상태 변경의 저장은 요청 처리에 성공한 라우트가 담당한다.
"""
from counseling.cards import pending
from counseling.rapport import apply_classification
from counseling.rapport import restart as rapport_restart

INTRO_QUESTION = "몇 가지 여쭤봐도 될까요?"
# 새 상담은 첫 인사에서 바로 알아가기 질문을 한다. 동의 단계는 이전 상담을 이어갈 때만 남는다.
INTRO_SUGGESTIONS = ["네, 여쭤보세요"]
# 진로 상담 중에 알아가기를 다시 시작할 때 사용자 메시지로 남기는 문장. 알아가기 답변 기록에서는 뺀다.
RESTART_TEXT = "알아가기를 다시 할게요."
STAGES = ("intro", "rapport", "cards", "career")


# 예전 코드가 카드만으로 만든 기준을 표시한다. 대화에서 정정·확인한 항목은 보존한다.
def _mark_legacy_card_criterion(state):
    cards = state["rapport"].get("cards")
    if not cards or not cards.get("chosen_id"):
        return
    item_id = cards.get("criterion_item_id")
    chosen = next((c for c in cards["offered"] if c["id"] == cards["chosen_id"]), None)
    if item_id is None and chosen:
        # ID 연결 전의 버전은 카드 선택 이벤트와 같은 턴에 자동 생성된 항목만 식별한다.
        matches = [i for i in state["criteria"] if i["text"] == chosen.get("criterion") and (
            i["turn"] == cards.get("selected_turn") or i["evidence"] == f"라포 카드 '{chosen['name']}' 선택")]
        if len(matches) == 1:
            item_id = matches[0]["id"]
    item = next((i for i in state["criteria"] if i["id"] == item_id), None)
    if item is None or "source" in item:
        return
    discussed = any(c["target_id"] == item_id for c in state["corrections"])
    item["source"] = "conversation" if discussed else "rapport_card"


# 이전 상담의 진행 상태를 추론하고 새 필드를 보완한다. 읽기만으로 원본 DB를 바꾸지는 않는다.
def ensure_flow(state):
    rapport = state["rapport"]
    cards = rapport.get("cards")
    if state.get("conversation_stage") not in STAGES:
        if pending(cards):
            stage = "cards"
        elif rapport["done"]:
            stage = "rapport" if state.get("controller", {}).get("pending_card_offer") else "career"
        elif rapport["questions"]:
            stage = "rapport"
        else:
            stage = "intro"
        state["conversation_stage"] = stage
    state.setdefault("career_concern_received", bool(state.get("controller", {}).get("direction", {}).get("label")))
    _mark_legacy_card_criterion(state)
    return state["conversation_stage"]


# 카드만으로 자동 등록된 과거 항목을 제외한다. 실제 대화에서 확인한 기준·경험·제약은 그대로 사용한다.
def conversation_items(state, key=None):
    keys = (key,) if key else ("experiences", "criteria", "constraints")
    return [item for section in keys for item in state[section] if item.get("source") != "rapport_card"]


# 카드를 확인한 뒤 남은 라포 질문을 정리하고 진로 상담으로 이동한다. 확인 대기·탐색 횟수는 보존한다.
def enter_career(state, turn, reason="card_selected"):
    rapport = state["rapport"]
    if not rapport["done"]:
        rapport.update(done=True, end_reason=reason, done_turn=turn)
    for question in rapport["questions"]:
        if question["status"] in ("pending", "asked"):
            question["status"] = "skipped"
    state["controller"]["pending_card_offer"] = None
    state["conversation_stage"] = "career"
    # 화면 구분과 모델 입력 정리에 쓰도록 채팅이 바뀐 턴과, 진로 상담이 처음 시작된 턴을 기록한다.
    # 시작 턴을 먼저 기록하면 room_switches가 그 턴을 기존 전환으로 보고 같은 전환을 두 번 넣으므로 순서를 지킨다.
    state["room_switches"] = room_switches(state) + [{"room": "career", "turn": turn}]
    state.setdefault("career_started_turn", turn)


# 진로 상담 중에 알아가기를 다시 시작한다. 이전 알아가기 기록과 사용자 정보는 보존하고 새 질문으로 시작한다.
def restart_rapport(state, turn):
    state["room_switches"] = room_switches(state) + [{"room": "rapport", "turn": turn}]
    state["rapport"] = rapport_restart(state["rapport"], turn)
    state["conversation_stage"] = "rapport"


# 알아가기·진로 상담 채팅이 바뀐 턴 목록. 시작 턴만 있는 이전 기록은 진로 상담 한 번으로 본다.
def room_switches(state):
    switches = state.get("room_switches")
    if switches is None and state.get("career_started_turn") is not None:
        switches = [{"room": "career", "turn": state["career_started_turn"]}]
    return list(switches or [])


# 메시지 턴이 어느 채팅에 속하는지 정한다. 첫 진로 상담 전은 모두 알아가기다.
def room_of(turn, switches):
    room = "rapport"
    for switch in switches:
        if turn >= switch["turn"]:
            room = switch["room"]
    return room


# 분류된 동의·고민 신호를 반영한다. 고민을 먼저 말해도 알아가기를 이어가며, 상담은 카드를 확인한 뒤 시작한다.
# 상담을 재촉하면 rapport가 남은 질문을 줄여 카드 단계로 넘기지만, 카드 없이 상담으로 가는 길은 두지 않는다.
def apply_input(state, result, turn):
    stage = ensure_flow(state)
    if result.follow_up == "close":
        return
    if result.concern_provided:
        state["career_concern_received"] = True
    if stage == "intro" and (result.rapport_consent or result.concern_provided or result.wants_concern_first):
        state["conversation_stage"] = "rapport"
        stage = "rapport"
    if stage == "rapport":
        state["rapport"] = apply_classification(state["rapport"], result, turn)


# 새 답변과 과거 상담 복원에 같은 표시 규칙을 적용한다. 라포·카드 단계에서는 예시 답변을 내보내지 않는다.
def suggestions_for_stage(state, suggestions, has_question=True):
    stage = ensure_flow(state)
    if not has_question or stage in ("rapport", "cards"):
        return []
    if stage == "intro":
        return list(INTRO_SUGGESTIONS)
    return list(suggestions)


# 알아가기 구간을 질문·사용자 답변 원문과 카드 선택 사실로만 정리한다.
# 챗봇이 덧붙인 해석·정리 문장은 넣지 않는다. 질문은 코드가 답변 끝에 붙인 마지막 문단에서 가져온다.
def _rapport_record(rows, selected):
    lines = ["## 진로 상담 전 알아가기 대화", "질문과 사용자 답변 원문이다. 챗봇이 덧붙인 해석과 정리는 뺐다."]
    question = None
    for row in rows:
        if row["role"] == "assistant":
            last = row["content"].rsplit("\n\n", 1)[-1].strip()
            question = last if last.endswith(("?", "？")) else None
        elif row["content"] != RESTART_TEXT:
            lines.append(f"- 질문: {question}\n  답변: {row['content']}" if question else f"- 답변: {row['content']}")
    if selected:
        lines.append(f"라포 카드: '{selected['name']}' (챗봇이 대화를 보고 고른 카드)")
    return "\n".join(lines)


# 과거 카드 설명이 사용자 발언처럼 모델에 전달되지 않도록, 정확히 일치하는 자동 기록만 정리한다.
# 진로 상담 중에는 알아가기 대화 전문 대신 모든 알아가기의 원문 기록 하나와 진로 상담 구간만 전달한다.
# 알아가기를 다시 하는 중에는 이번 알아가기 구간만 전달한다.
def history_for_model(rows, state):
    cards = state["rapport"].get("cards")
    selected = next((c for c in cards["offered"] if c["id"] == cards.get("chosen_id")), None) if cards else None
    switches = room_switches(state)
    history = []
    if switches and state.get("conversation_stage") == "career":
        rapport_rows = [row for row in rows if room_of(row["turn"], switches) == "rapport"]
        if any(row["role"] == "user" for row in rapport_rows):
            history.append({"role": "developer", "content": _rapport_record(rapport_rows, selected)})
            rows = [row for row in rows if room_of(row["turn"], switches) == "career"]
    elif switches:
        rows = [row for row in rows if row["turn"] >= switches[-1]["turn"]]
    for row in rows:
        content = row["content"]
        if selected and row["role"] == "user":
            old = f"'{selected['name']}' 카드를 골랐어요. ({selected.get('meaning', '')})"
            if content == old:
                content = f"라포 카드 선택 기록: {selected['name']}"
        history.append({"role": row["role"], "content": content})
    return history


# 저장된 진로맵에서도 카드만으로 추가된 과거 기준과 그 기준을 인용한 비교 칸을 제외한다.
def without_card_criteria(career_map, state):
    if career_map is None:
        return None
    excluded = {i["id"] for i in state["criteria"] if i.get("source") == "rapport_card"}
    return {
        **career_map,
        "criteria": [i for i in career_map["criteria"] if i["id"] not in excluded],
        "candidates": [{**candidate, "by_criteria": [note for note in candidate["by_criteria"]
                                                     if note["criterion_id"] not in excluded]}
                       for candidate in career_map["candidates"]],
    }
