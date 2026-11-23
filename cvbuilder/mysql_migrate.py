"""Apply the reviewed MySQL schema inside the database VPC.

The Lambda handler is invoked explicitly after provisioning RDS. It has no HTTP
route and returns only the schema version, never credentials or application data.
"""
from __future__ import annotations

from pathlib import Path

from .mysql_storage import MySQLStore

SCHEMA_FILES = tuple(sorted((Path(__file__).resolve().parents[1] / "migrations").glob("[0-9][0-9][0-9]_*.sql")))
SCHEMA_VERSION = SCHEMA_FILES[-1].stem


def _statements(source: str) -> list[str]:
    """Split this repository's simple DDL file, excluding whole-line comments."""
    lines = [line for line in source.splitlines() if not line.lstrip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def apply_schema() -> bool:
    connection = MySQLStore("schema-migration")._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(_statements(SCHEMA_FILES[0].read_text(encoding="utf-8"))[0])
            applied = False
            for file in SCHEMA_FILES:
                cursor.execute("SELECT version FROM schema_migrations WHERE version=%s", (file.stem,))
                if cursor.fetchone() is not None:
                    continue
                statements = _statements(file.read_text(encoding="utf-8"))
                for statement in statements[1:] if file == SCHEMA_FILES[0] else statements:
                    cursor.execute(statement)
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (file.stem,))
                connection.commit()
                applied = True
        return applied
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def handler(_event, _context):
    return {"version": SCHEMA_VERSION, "applied": apply_schema()}


if __name__ == "__main__":
    print(handler({}, None))
