"""The built UI served by the API: client routes get index.html, revalidated."""

from fastapi.testclient import TestClient

from c2ai.app import create_app


def test_index_is_revalidated_so_a_new_release_is_picked_up(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<script src="/assets/index-abc.js"></script>')
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)")
    monkeypatch.setenv("C2AI_FRONTEND_DIST", str(tmp_path))
    client = TestClient(create_app(serve_frontend=True))

    page = client.get("/login")
    assert page.status_code == 200
    assert "index-abc.js" in page.text
    assert page.headers["cache-control"] == "no-cache"
    # Bundles are content-hashed; they need no such header.
    assert "cache-control" not in client.get("/assets/index-abc.js").headers
    assert client.get("/auth/nope").status_code == 404
