# CareerMate

SLA451 팀 프로젝트. 정답을 대신 제시하는 대신, 사용자가 자신의 경험과 가치관을 탐색해 **스스로 진로 선택 기준을 세우도록 돕는 자기 성찰형 진로 탐색 챗봇**입니다.

## 준비물

- [Python](https://www.python.org/downloads/) 3.12 이상
- [Node.js](https://nodejs.org/) 20 이상
- OpenAI API 키

## 설치 (처음 한 번만)

프로젝트 폴더에서 아래를 순서대로 실행합니다.

**1. API 키 넣기**

`.env.example`을 복사해 `.env`를 만들고 키를 채웁니다.

```bash
cp .env.example .env
```

```
OPENAI_API_KEY=발급받은 키
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=low
```

> `.env`는 git에 올라가지 않습니다. 키를 코드나 README에 직접 적지 마세요.

**2. 패키지 설치**

Windows (cmd)

```bat
python -m venv venv
venv\Scripts\activate
python -m pip install -r backend\requirements.txt
cd frontend && npm install && cd ..
```

macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

## 실행

터미널 두 개가 필요합니다. 둘 다 켜져 있어야 합니다.

**터미널 1 (서버)**

```bash
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

cd backend
uvicorn main:app --reload
```

**터미널 2 (화면)**

```bash
cd frontend
npm run dev
```

브라우저에서 [http://localhost:5173/](http://localhost:5173/)을 엽니다. 종료는 각 터미널에서 `Ctrl+C`입니다.

## 사용 방법

- 첫 화면에서 이름이나 호칭과 윤이의 말투(5년지기 친구·차분한 분석가·매운맛 조언가)를 고르면 윤이가 먼저 인사를 건네며 상담이 시작됩니다. 새로고침해도 이어지고, **새 상담** 버튼을 누르면 이름 화면부터 다시 시작합니다. 다른 브라우저나 시크릿 창은 별개의 상담입니다.
- 오른쪽 **기준·제약** 탭에는 대화에서 확인된 경험, 선택 기준, 제약이 정리됩니다. 챗봇이 추정했거나 사용자가 유보한 항목은 **미확인**으로 표시됩니다. 고치고 싶은 내용은 대화로 말하면 반영됩니다.
- **진로맵** 탭의 **현재까지 정리하기**를 누르면 선택 기준, 후보 비교, 다음 탐색 행동을 정리합니다. 정리 후 대화를 더 하면 "추가 대화 미반영" 표시가 뜨고, 다시 누르면 갱신됩니다.

## 개발 참고

- `backend/`는 FastAPI + SQLite, `frontend/`는 React + Vite입니다. 대화 기록은 `backend/chatbot.db`에 저장됩니다.
- 백엔드 폴더: `routes/`(요청 처리), `counseling/`(상담 규칙, 모델 없이 테스트), `llm/`(모델 호출·프롬프트·말투), `storage/`(DB). 프론트 폴더: `screens/`(이름 화면, 상담 화면), `components/`(말풍선, 입력창, 카드, 패널), `styles/`, `assets/`.
- 상태 병합과 진로맵 조립 로직 테스트: `python -m pytest backend/tests -q`
