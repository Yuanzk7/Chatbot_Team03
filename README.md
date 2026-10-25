# CareerMate

SLA451 팀 프로젝트. 정답을 대신 제시하는 대신, 사용자가 자신의 경험과 가치관을 탐색해 **스스로 진로 선택 기준을 세우도록 돕는 자기 성찰형 진로 탐색 챗봇**입니다.

## 구조

```
backend/            FastAPI + SQLite
  main.py           API (상담 생성·조회, 메시지 전송, 정리)
  llm.py            OpenAI 호출 (분류 / 응답 / 진로맵 정리)
  state.py          상담 상태(경험·기준·제약) 스키마와 병합 로직
  career_map.py     진로맵 스키마와 조립 로직
  prompts.py        시스템 프롬프트, 분류·정리 프롬프트, 라포 질문 50개
  db.py             SQLite 테이블
  tests/            병합·진로맵 로직 단위 테스트
frontend/           React + Vite
  src/App.jsx       채팅 화면
  src/SidePanel.jsx 우측 패널 (기준·제약 탭 / 진로맵 탭)
```

메시지 한 번에 모델을 두 번 호출합니다. 먼저 분류 호출이 사용자 입력에서 경험·선택 기준·제약과 정정을 뽑아 상태에 반영하고, 그 상태를 붙여 응답을 생성합니다. "현재까지 정리하기" 버튼은 전체 대화와 상태로 선택 기준·후보 비교·다음 탐색 행동을 정리합니다.

## 시작하기

### 1. 필요한 프로그램

- [Python](https://www.python.org/downloads/) 3.12 이상
- [Node.js](https://nodejs.org/) 20 이상

### 2. API 키 설정

프로젝트 최상위 폴더에서 `.env.example`을 복사해 `.env`를 만들고 OpenAI API 키를 채웁니다.

```bash
cp .env.example .env
```

```
OPENAI_API_KEY=발급받은 키
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=low
```

> `.env`는 git에 올리지 않습니다. 키를 코드나 README에 직접 적지 마세요.

### 3. 백엔드 실행

프로젝트 최상위 폴더에서 가상환경을 만들고 패키지를 설치합니다. 처음 한 번만 하면 됩니다.

**Windows (cmd)**

```bat
python -m venv venv
venv\Scripts\activate
python -m pip install -r backend\requirements.txt
```

**macOS / Linux**

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r backend/requirements.txt
```

서버를 실행합니다.

```bash
cd backend
uvicorn main:app --reload
```

[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)에서 API 문서를 확인할 수 있습니다. 데이터는 `backend/chatbot.db`(SQLite)에 저장되며 git에 올라가지 않습니다.

### 4. 프론트엔드 실행

**새 터미널**에서 실행합니다. `npm install`은 처음 한 번만 하면 됩니다.

```bash
cd frontend
npm install
npm run dev
```

[http://localhost:5173/](http://localhost:5173/)에 접속합니다. 프론트엔드 개발 서버가 `/api` 요청을 백엔드(8000번 포트)로 넘겨주므로 두 서버가 모두 켜져 있어야 합니다.

### 5. 테스트

모델 호출 없이 상태 병합과 진로맵 조립 로직을 검사합니다.

```bash
python -m pytest backend/tests -q
```

## 사용 방법

- 첫 접속 시 상담 ID가 발급되어 브라우저에 저장됩니다. 새로고침해도 같은 상담이 이어지고, **새 상담** 버튼을 누르면 새 상담이 시작됩니다. 다른 브라우저나 시크릿 창은 별개의 상담입니다.
- 오른쪽 **기준·제약** 탭에는 대화에서 확인된 경험·선택 기준·제약이 턴마다 갱신됩니다. 챗봇의 추정이나 사용자가 유보한 항목은 **미확인**으로 표시되고, 철회한 항목은 정정 내역에만 남습니다. 수정은 대화로 합니다.
- **진로맵** 탭의 **현재까지 정리하기** 버튼을 누르면 선택 기준, 후보 비교, 다음 탐색 행동을 정리합니다. 정리 후 대화를 더 나누면 "추가 대화 미반영" 표시가 뜨고, 버튼을 다시 누르면 갱신됩니다.
