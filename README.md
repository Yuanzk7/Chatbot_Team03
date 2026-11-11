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

- 첫 화면에서 이름이나 호칭을 입력하면 윤이가 먼저 인사를 건네며 상담이 시작됩니다. 새로고침해도 이어지고, **새 상담** 버튼을 누르면 이름 화면부터 다시 시작합니다. 다른 브라우저나 시크릿 창은 별개의 상담입니다.
- 윤이는 첫 인사에서 바로 첫 알아가기 질문을 합니다. 알아가기 질문에는 예시 답변 없이 직접 입력하며, 질문 하나는 건너뛸 수 있고, 진로 상담을 바로 원한다고 하면 남은 질문을 줄여 카드 단계로 넘어갑니다. 카드를 거치지 않고 진로 상담으로 넘어갈 수는 없습니다. 먼저 말한 진로 고민은 기억해 두었다가 진로 상담에서 이어서 다룹니다.
- 알아가기를 마치면 윤이가 대화를 보고 고른 카드 한 장을 보여줍니다. 카드는 대화의 참고 정보이며 진로 기준으로 쓰지 않습니다. **진로 상담 시작하기**를 누르면 **진로 상담** 채팅으로 넘어가고, 고른 카드는 화면 위 이름 옆에 표시됩니다. 이때 알아가기 채팅은 비워지지만 나눈 이야기는 상담에 계속 참고합니다.
- 새로고침하거나 다시 열면 **알아가기 / 진로 상담** 목록이 먼저 뜨고, 고른 채팅으로 들어갑니다. 채팅 위쪽의 **← 목록**으로 돌아올 수 있습니다. 진로 상담은 카드를 확인하기 전까지 잠겨 있습니다.
- 진로 상담 중에 **알아가기**를 다시 고르면 새 라포 카드가 만들어진다는 안내 후 새 질문으로 알아가기를 다시 합니다. 그동안 진로 상담 채팅은 읽기만 할 수 있고, 새 카드를 확인하면 이전 상담으로 돌아갑니다.
- 오른쪽 위 **진로맵** 버튼으로 정리 화면을 열고 닫을 수 있습니다. **현재까지 정리하기**를 누르면 선택 기준, 후보 비교, 다음 탐색 행동을 정리합니다. 고치고 싶은 내용은 대화로 말한 뒤 다시 정리하면 됩니다. 정리 후 대화를 더 하면 "추가 대화 미반영" 표시가 뜨고, **다시 정리하기**를 누르면 갱신됩니다.
