"""서버 시작점. 앱을 만들고 테이블을 준비한 뒤 라우트를 등록한다.
실행: backend 폴더에서 `uvicorn main:app --reload`

폴더 구성
- routes/      화면에서 오는 요청 처리 (상담, 메시지, 라포 카드, 정리)
- counseling/  상담 규칙. 모델 호출 없이 단위 테스트되는 부분 (상태, 서로 알아가기, 카드, 진로맵)
- llm/         모델 호출과 프롬프트, 윤이의 말투
- storage/     데이터베이스
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from routes import cards, messages, sessions, summary
from storage.db import init_db

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


# 서버 시작 시 DB를 준비한다. 모듈을 읽거나 테스트 앱을 만드는 것만으로 DB를 변경하지 않는다.
@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


# Render가 서버의 실행 상태를 확인한다. 모델 API 호출 없이 응답한다.
def health():
    return {"status": "ok"}


# API를 먼저 등록하고 빌드된 화면이 있으면 같은 주소에서 제공한다.
# 로컬 개발에서는 기존 Vite 서버를 그대로 사용할 수 있다.
def create_app(frontend_dir=FRONTEND_DIST):
    app = FastAPI(lifespan=lifespan)
    for module in (sessions, messages, cards, summary):
        app.include_router(module.router)
    app.add_api_route("/health", health, methods=["GET"])
    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app


app = create_app()
