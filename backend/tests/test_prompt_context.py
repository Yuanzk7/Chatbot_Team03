"""프롬프트를 줄여도 분류·정정에 필요한 맥락과 현재 확인 상태가 보존되는지 검사한다."""
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from counseling.controller import empty_controller  # noqa: E402
from counseling.state import Classification, empty_state, format_state  # noqa: E402
from llm import client  # noqa: E402


# 현재 항목, 철회, 카드 자동 항목과 정정 이력이 공존하는 상담을 만든다.
def recorded_state(stage):
    state = empty_state()
    state.update(preferred_name="유찬", conversation_stage=stage)
    state["experiences"] = [dict(id=1, text="모델 구현", status="confirmed", evidence="구현해봤어", turn=1)]
    state["criteria"] = [dict(id=2, text="분석 중심 연구", status="unconfirmed", evidence="확실하지 않아", turn=2),
                         dict(id=4, text="카드 자동 기준", status="confirmed", evidence="카드 선택", turn=1,
                              source="rapport_card")]
    state["constraints"] = [dict(id=3, text="졸업 직후 취업", status="withdrawn", evidence="그럴 필요 없어", turn=2)]
    state["corrections"] = [dict(target_id=2, turn=2, before=dict(text="빠른 성과", status="confirmed"),
                                 after=dict(text="분석 중심 연구", status="unconfirmed"), evidence="꼭 빠를 필요 없어")]
    state["rapport"].update(done=stage == "career", category="unist", questions=[
        dict(id=17, text="가장 재미있었던 경험은?", status="asked", asks=1, holds=0),
        dict(id=45, text="가장 재미있었던 수업은?", status="pending", asks=0, holds=0),
    ])
    controller = empty_controller()
    controller["direction"].update(label="대학원 진학", turns=7)
    controller["last_question"] = dict(type="confirm", item_ids=[2], text="분석 중심 연구가 중요하다는 이해가 맞나요?")
    controller["pending_confirmation_ids"] = [2]
    controller["asked_confirmations"] = {"2": "2:분석 중심 연구"}
    state["controller"] = controller
    return state


# 실제 분류 호출의 직렬화 결과에서 필요한 항목·직전 질문은 남고 내부 장부는 빠지는지 확인한다.
@pytest.mark.parametrize("stage", ["rapport", "career"])
def test_classify_sends_current_items_and_question_without_internal_bookkeeping(monkeypatch, stage):
    state = recorded_state(stage)
    before = deepcopy(state)
    model = Mock()
    monkeypatch.setattr(client, "_parse", model)
    client.classify(state, [], "응, 맞아")
    instructions, messages, schema, label = model.call_args.args
    context = json.loads(instructions.split("## 현재 state\n", 1)[1])
    assert schema is Classification and label == "classify"
    assert messages == [{"role": "user", "content": "응, 맞아"}]
    assert context["criteria"] == [dict(id=2, text="분석 중심 연구", status="unconfirmed")]
    assert context["constraints"] == [dict(id=3, text="졸업 직후 취업", status="withdrawn")]
    assert context["controller"]["last_question"] == state["controller"]["last_question"]
    assert context["controller"]["direction"] == dict(label="대학원 진학", awaiting_choice=False)
    assert "corrections" not in context
    assert "asked_confirmations" not in context["controller"]
    questions = context["rapport"]["questions"]
    assert [q["id"] for q in questions] == ([17, 45] if stage == "rapport" else [])
    assert state == before


# 같은 정정을 여러 번 기록해도 응답 모델은 현재 상태를 한 번만 보고, 원본 이력은 보존된다.
def test_reply_context_uses_current_status_without_replaying_correction_history():
    state = recorded_state("career")
    once = format_state(state)
    state["corrections"] *= 100
    assert format_state(state) == once
    assert "id 2: 분석 중심 연구" in once
    assert "빠른 성과" not in once
    assert "[철회한 정보 (withdrawn)] id 3: 졸업 직후 취업" in once
    assert "카드 자동 기준" not in once
    assert len(state["corrections"]) == 100
