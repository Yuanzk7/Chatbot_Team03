import json
import os
from pathlib import Path
from typing import List, Optional

import openai
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from counseling.career_map import CareerMapOutput, active_criteria
from counseling.controller import finalize_reply
from counseling.exploration import ExplorationOutput, classification_context
from counseling.state import Classification, classification_state, format_state
from llm.prompts import CLASSIFY_PROMPT, SUMMARY_PROMPT, SYSTEM_PROMPT, TONE_PROMPT

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")  # 프로젝트 루트의 .env

MODEL = os.getenv("OPENAI_MODEL", "")
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "")
TIMEOUT_SECONDS = 60
# 최신 정보가 필요한 턴에만 쓰는 웹 검색 설정. 범위는 제한하지 않고 한국 기준으로 찾으며, 턴당 검색은 한 번이다.
WEB_SEARCH_OPTIONS = {
    "tools": [{"type": "web_search", "search_context_size": "low",
               "user_location": {"type": "approximate", "country": "KR"}}],
    "max_tool_calls": 1,
}


class LLMConfigError(Exception):
    pass


class LLMResponseError(Exception):
    """모델이 미완료(incomplete) 상태이거나 빈 답변을 돌려준 경우."""


# API 키와 모델 설정을 확인한 뒤, 제한 시간이 적용된 클라이언트를 만든다.
def _client():
    if not os.getenv("OPENAI_API_KEY"):
        raise LLMConfigError("API 키가 설정되지 않았습니다. .env의 OPENAI_API_KEY를 확인하세요.")
    if not MODEL:
        raise LLMConfigError("모델이 설정되지 않았습니다. .env의 OPENAI_MODEL을 확인하세요.")
    return OpenAI(timeout=TIMEOUT_SECONDS)


# 추론 강도를 설정한 경우에만 API 옵션에 포함한다.
def _reasoning_kwargs():
    return {"reasoning": {"effort": REASONING_EFFORT}} if REASONING_EFFORT else {}


# 아직 저장되지 않은 이번 입력을 기존 대화 뒤에 붙여 분류와 응답에 동일하게 전달한다.
def _messages(history, user_message):
    """저장된 전체 대화 + 아직 저장되지 않은 현재 입력."""
    return history + [{"role": "user", "content": user_message}]


# Pydantic 스키마로 구조화 출력을 받고, 잘못된 JSON이나 미완료 응답을 공통 오류로 변환한다.
# options는 웹 검색처럼 이번 호출에만 붙이는 도구 설정이며, 검색 횟수를 셀 수 있도록 응답 전체를 돌려준다.
def _respond(instructions, messages, text_format, label, **options):
    """구조화 출력 호출. 파싱·검증 실패와 미완료 응답은 LLMResponseError로 통일한다."""
    client = _client()
    try:
        response = client.responses.parse(
            model=MODEL,
            instructions=instructions,
            input=messages,
            text_format=text_format,
            **_reasoning_kwargs(),
            **options,
        )
    except ValueError as e:
        # pydantic ValidationError와 json.JSONDecodeError 모두 ValueError의 하위 클래스
        raise LLMResponseError(f"{label} parse failed: {e}") from e
    if response.status != "completed" or response.output_parsed is None:
        raise LLMResponseError(f"{label} status={response.status}")
    return response


# 도구 없이 구조화 출력만 받는 일반 호출.
def _parse(instructions, messages, text_format, label):
    return _respond(instructions, messages, text_format, label).output_parsed


# 사용자 정보·정정과 함께 요청 유형·탐색 방향·확인 응답을 분류한다. 규칙 판정은 하지 않는다.
def classify(state, history, user_message) -> Classification:
    """분류 결과는 controller가 상태를 갱신하고 이번 행동을 결정할 때 사용한다."""
    instructions = CLASSIFY_PROMPT + "\n## 현재 state\n" + json.dumps(classification_state(state), ensure_ascii=False)
    return _parse(instructions, _messages(history, user_message), Classification, "classify")


# 정리 버튼을 눌렀을 때만 전체 대화에서 후보 비교와 다음 행동을 만든다.
def summarize(state, history) -> CareerMapOutput:
    """전체 대화와 활성 선택 기준으로 후보 비교·다음 탐색 행동을 정리한다 (MVP_Design.md 3.4)."""
    instructions = (
        SUMMARY_PROMPT
        + "\n## 선택 기준\n"
        + json.dumps(active_criteria(state), ensure_ascii=False)
        + "\n## 상담 진행·행동 기록\n"
        + json.dumps(classification_context(state), ensure_ascii=False)
    )
    return _parse(instructions, history, CareerMapOutput, "summary")


class CardPick(BaseModel):
    card_id: int
    evidence: str


class Recommendation(BaseModel):
    """개인화된 후보 추천은 본문과 분리해, 확인된 경험·기준을 근거로 삼았는지 검사한다."""
    candidate: str
    reason: str
    basis_ids: List[int]


class Reply(BaseModel):
    content: str
    question: Optional[str]
    suggestions: List[str]
    asked_question_id: Optional[int]
    rapport_cards: List[CardPick]
    basis_ids: List[int]
    recommendations: List[Recommendation]
    exploration: Optional[ExplorationOutput] = None


# 컨트롤러 계획을 모델에 전달하고, 근거 검증·고정 질문 조립까지 마친 응답 객체를 반환한다.
def generate_reply(state, history, user_message, turn, plan):
    """모델 호출 뒤 추가 API 호출 없이 controller.finalize_reply로 결과를 검사한다.
    웹 검색 턴에서 검색 호출이 실패한 경우에만, 찾지 못했다고 알리도록 지시를 바꿔 검색 없이 한 번 더 호출한다."""
    messages = _messages(history, user_message)
    searches = 0
    if plan.get("web_search"):
        try:
            response = _respond(_reply_instructions(state, turn, plan), messages, Reply, "reply", **WEB_SEARCH_OPTIONS)
            reply = response.output_parsed
            searches = sum(1 for item in response.output if item.type == "web_search_call")
        except (openai.APIError, LLMResponseError):
            plan["web_search_failed"] = True
            reply = _parse(_reply_instructions(state, turn, plan), messages, Reply, "reply")
    else:
        reply = _parse(_reply_instructions(state, turn, plan), messages, Reply, "reply")
    controlled = finalize_reply(state, plan, reply)
    controlled.searches = searches
    return controlled


# 공통 원칙·말투·이번 턴 계획을 합친 응답 지시. 검색 실패 여부가 바뀌면 다시 만든다.
def _reply_instructions(state, turn, plan):
    return (SYSTEM_PROMPT + "\n" + TONE_PROMPT.format(name=state.get("preferred_name") or "사용자")
            + "\n" + format_state(state, turn, plan))
