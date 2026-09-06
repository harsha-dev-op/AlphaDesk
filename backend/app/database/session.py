from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

settings = get_settings()

engine_options: dict[str, object] = {"pool_pre_ping": True}
if settings.database_url == "sqlite+pysqlite:///:memory:":
    engine_options.update(connect_args={"check_same_thread": False}, poolclass=StaticPool)
elif settings.database_url.startswith("sqlite"):
    engine_options.update(connect_args={"check_same_thread": False})
else:
    engine_options.update(pool_size=5, max_overflow=10)

engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
