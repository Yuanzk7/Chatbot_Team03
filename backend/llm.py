import json
import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from career_map import CareerMapOutput, active_criteria
from prompts import CLASSIFY_PROMPT, PERSONAS, SUMMARY_PROMPT, SYSTEM_PROMPT
from state import Classification, format_state

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL = os.getenv("OPENAI_MODEL", "")
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "")
TIMEOUT_SECONDS = 60


class LLMConfigError(Exception):
    pass


class LLMResponseError(Exception):
    """모델이 미완료(incomplete) 상태이거나 빈 답변을 돌려준 경우."""


def _client():
    if not os.getenv("OPENAI_API_KEY"):
        raise LLMConfigError("API 키가 설정되지 않았습니다. .env의 OPENAI_API_KEY를 확인하세요.")
    if not MODEL:
        raise LLMConfigError("모델이 설정되지 않았습니다. .env의 OPENAI_MODEL을 확인하세요.")
    return OpenAI(timeout=TIMEOUT_SECONDS)


def _reasoning_kwargs():
    return {"reasoning": {"effort": REASONING_EFFORT}} if REASONING_EFFORT else {}


def _messages(history, user_message):
    """저장된 전체 대화 + 아직 저장되지 않은 현재 입력."""
    return history + [{"role": "user", "content": user_message}]


def _parse(instructions, messages, text_format, label):
    """구조화 출력 호출. 파싱·검증 실패와 미완료 응답은 LLMResponseError로 통일한다."""
    client = _client()
    try:
        response = client.responses.parse(
            model=MODEL,
            instructions=instructions,
            input=messages,
            text_format=text_format,
            **_reasoning_kwargs(),
        )
    except ValueError as e:
        # pydantic ValidationError와 json.JSONDecodeError 모두 ValueError의 하위 클래스
        raise LLMResponseError(f"{label} parse failed: {e}") from e
    if response.status != "completed" or response.output_parsed is None:
        raise LLMResponseError(f"{label} status={response.status}")
    return response.output_parsed


def classify(state, history, user_message) -> Classification:
    """현재 입력에서 새 경험·기준·제약과 정정을 추출한다 (MVP_Design.md 3.3)."""
    instructions = CLASSIFY_PROMPT + "\n## 현재 state\n" + json.dumps(state, ensure_ascii=False)
    return _parse(instructions, _messages(history, user_message), Classification, "classify")


def summarize(state, history) -> CareerMapOutput:
    """전체 대화와 활성 선택 기준으로 후보 비교·다음 탐색 행동을 정리한다 (MVP_Design.md 3.4)."""
    instructions = (
        SUMMARY_PROMPT
        + "\n## 선택 기준\n"
        + json.dumps(active_criteria(state), ensure_ascii=False)
    )
    return _parse(instructions, history, CareerMapOutput, "summary")


class CardPick(BaseModel):
    card_id: int
    evidence: str


class Reply(BaseModel):
    content: str
    suggestions: List[str]
    asked_question_id: Optional[int]
    rapport_cards: List[CardPick]


MAX_SUGGESTIONS = 3
MAX_SUGGESTION_CHARS = 30


def generate_reply(state, history, user_message, turn, plan):
    """갱신된 state와 이번 턴의 질문 계획(plan_turn 결과)을 시스템 프롬프트에 붙여 답변을 생성한다.
    (content, suggestions, asked_question_id, rapport_cards)를 돌려준다."""
    reply = _parse(
        SYSTEM_PROMPT
        + "\n"
        + PERSONAS.get(state.get("tone"), PERSONAS["counselor"])["tone"].format(name=state.get("preferred_name") or "사용자")
        + "\n"
        + format_state(state, turn, plan),
        _messages(history, user_message),
        Reply,
        "reply",
    )
    content = reply.content.strip()
    if not content:
        raise LLMResponseError("reply content empty")
    suggestions = []
    for s in reply.suggestions:
        s = s.strip()
        if s and len(s) <= MAX_SUGGESTION_CHARS and s not in suggestions:
            suggestions.append(s)
    return content, suggestions[:MAX_SUGGESTIONS], reply.asked_question_id, reply.rapport_cards
