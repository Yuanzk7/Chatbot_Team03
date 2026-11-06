from fastapi.testclient import TestClient

from main import create_app


# 같은 서버에서 빌드된 화면과 API를 제공하고, 새 상담을 HTTP로 다시 읽을 수 있다.
def test_frontend_and_api_share_one_origin(database, tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>CareerMate build</html>")
    with TestClient(create_app(frontend)) as client:
        assert "CareerMate build" in client.get("/").text
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/api/sessions", json={"preferred_name": "배포 검증"})
        assert response.status_code == 200
        session = response.json()
        restored = client.get(f"/api/sessions/{session['session_id']}")
        assert restored.status_code == 200
        assert restored.json()["messages"][0] == session["message"]
        assert client.get("/api/missing").status_code == 404


# 화면 빌드가 없는 로컬 개발 환경에서도 API와 상태 확인은 정상 동작한다.
def test_api_runs_without_frontend_build(database, tmp_path):
    with TestClient(create_app(tmp_path / "missing-dist")) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 404
        assert client.post("/api/sessions", json={"preferred_name": "로컬 검증"}).status_code == 200
