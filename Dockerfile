# 화면을 빌드한 뒤 결과물만 Python 실행 이미지에 복사한다.
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
WORKDIR /app/backend
EXPOSE 10000
# 상담별 동시 요청 제어가 한 프로세스 안에서 동작하므로 worker는 기본값 1개를 사용한다.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port \"${PORT:-10000}\""]
