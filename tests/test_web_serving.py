from __future__ import annotations

from fastapi.testclient import TestClient

from soulsaka.config import get_settings
from soulsaka.hub.app import create_app, find_web_dir


def _fake_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>soulsaka</title><div id=app></div>")
    (dist / "assets" / "app-abc123.js").write_text("console.log('hi')")
    (dist / "manifest.webmanifest").write_text('{"name": "soulsaka"}')
    (dist / "sw.js").write_text("self.addEventListener('fetch', () => {})")
    return dist


def test_spa_served_with_fallback_and_assets(data_dir, tmp_path, monkeypatch, state):
    dist = _fake_dist(tmp_path)
    monkeypatch.setenv("SOULSAKA_WEB_DIR", str(dist))
    settings = get_settings()
    assert find_web_dir(settings) == dist
    app = create_app(settings, state=state, start_workers=False)
    with TestClient(app, client=("127.0.0.1", 40001)) as c:
        c.headers["X-Soulsaka-Client"] = "test"
        assert "<title>soulsaka</title>" in c.get("/").text
        # client-side routes fall back to index.html
        assert "<title>soulsaka</title>" in c.get("/memories").text
        assert "<title>soulsaka</title>" in c.get("/rate/v3").text
        # real files are served by name
        assert c.get("/assets/app-abc123.js").text.startswith("console.log")
        assert c.get("/manifest.webmanifest").json()["name"] == "soulsaka"
        assert "fetch" in c.get("/sw.js").text
        # the API keeps its own 404s and still works
        assert c.get("/api/does-not-exist").status_code == 404
        assert c.get("/api/health").json()["ok"] is True
        # no path traversal out of dist
        assert "<title>soulsaka</title>" in c.get("/../../etc/passwd").text


def test_without_web_build_root_explains(data_dir, state, monkeypatch):
    import soulsaka.hub.app as app_module

    monkeypatch.setattr(app_module, "find_web_dir", lambda settings: None)
    app = create_app(get_settings(), state=state, start_workers=False)
    with TestClient(app, client=("127.0.0.1", 40002)) as c:
        body = c.get("/").json()
        assert body["hub"] == "soulsaka" and "not built" in body["web_ui"]
