from app.core.config import Settings


def test_documented_comma_separated_cors_configuration(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:3000", "http://localhost:5173"]
