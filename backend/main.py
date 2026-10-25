"""서버 시작점. 앱을 만들고 테이블을 준비한 뒤 라우트를 등록한다.
실행: backend 폴더에서 `uvicorn main:app --reload`

폴더 구성
- routes/      화면에서 오는 요청 처리 (상담, 메시지, 라포 카드, 정리)
- counseling/  상담 규칙. 모델 호출 없이 단위 테스트되는 부분 (상태, 서로 알아가기, 카드, 진로맵)
- llm/         모델 호출과 프롬프트, 윤이의 말투
- storage/     데이터베이스
"""
from fastapi import FastAPI

from routes import cards, messages, sessions, summary
from storage.db import init_db

app = FastAPI()
init_db()

for module in (sessions, messages, cards, summary):
    app.include_router(module.router)
