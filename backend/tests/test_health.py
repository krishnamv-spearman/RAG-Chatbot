from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_reports_degraded_without_pipeline(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path),
                     pipeline_factory=lambda: (_ for _ in ()).throw(RuntimeError("no index")))
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert isinstance(body["llm_configured"], bool)


def test_llm_not_configured_without_key():
    settings = Settings(_env_file=None, llm_api_key=None)
    assert settings.llm_configured is False


def test_llm_not_configured_with_blank_key():
    settings = Settings(_env_file=None, llm_api_key="   ")
    assert settings.llm_configured is False


def test_cors_origins_parsed_from_comma_list():
    settings = Settings(_env_file=None, cors_origins="http://a.test, http://b.test,")
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_llm_configured_with_key():
    settings = Settings(_env_file=None, llm_api_key="gsk_test")
    assert settings.llm_configured is True
