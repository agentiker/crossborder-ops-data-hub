from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.app import _SPAStaticFiles


def test_spa_html_is_not_cached(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    app = FastAPI()
    app.mount("/app", _SPAStaticFiles(directory=str(tmp_path), html=True), name="spa")

    r = TestClient(app).get("/app/scheduled/system")

    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"


def test_missing_asset_does_not_fallback_to_index(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    app = FastAPI()
    app.mount("/app", _SPAStaticFiles(directory=str(tmp_path), html=True), name="spa")

    r = TestClient(app).get("/app/assets/old.js")

    assert r.status_code == 404
