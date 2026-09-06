from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.core.config import Settings


def _inside_project(path: Path) -> bool:
    project_root = Path(__file__).resolve().parents[3]
    try:
        path.relative_to(project_root)
        return True
    except ValueError:
        return False


def probe(env_file: Path) -> dict[str, object]:
    resolved = env_file.resolve()
    if not _inside_project(resolved):
        return {"status": "REFUSED", "reason": "env file must be inside the AlphaDesk project"}
    if not resolved.is_file():
        return {"status": "CONFIG_MISSING", "config_source": str(resolved)}
    settings = Settings(_env_file=resolved)
    url = make_url(settings.database_url)
    result: dict[str, object] = {
        "status": "NOT_CONNECTED",
        "config_source": str(resolved),
        "driver": url.drivername,
        "host": url.host,
        "port": url.port,
        "database": url.database,
        "username": url.username,
        "server_reachable": False,
        "authenticated": False,
    }
    if not url.drivername.startswith("postgresql"):
        result["status"] = "NOT_POSTGRESQL"
        return result
    try:
        with socket.create_connection((url.host or "localhost", url.port or 5432), timeout=2):
            result["server_reachable"] = True
    except OSError:
        result["status"] = "SERVER_UNREACHABLE"
        return result
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            row = connection.execute(text("select current_database(), current_user")).one()
            result["status"] = "CONNECTED"
            result["authenticated"] = True
            result["connected_database"] = row[0]
            result["connected_user"] = row[1]
    except OperationalError as error:
        message = str(error).lower()
        result["status"] = "AUTHENTICATION_FAILED" if "password authentication failed" in message else "DATABASE_ERROR"
    finally:
        engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely verify project-local PostgreSQL settings without printing secrets.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    print(json.dumps(probe(args.env_file), indent=2))


if __name__ == "__main__":
    main()
