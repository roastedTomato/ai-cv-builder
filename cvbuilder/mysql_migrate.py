"""Apply the reviewed MySQL schema inside the database VPC.

The Lambda handler is invoked explicitly after provisioning RDS. It has no HTTP
route and returns only the schema version, never credentials or application data.
"""
from __future__ import annotations

from pathlib import Path

from .mysql_storage import MySQLStore

SCHEMA_VERSION = "001_initial_mysql"
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "migrations" / (SCHEMA_VERSION + ".sql")


def _statements(source: str) -> list[str]:
    """Split this repository's simple DDL file, excluding whole-line comments."""
    lines = [line for line in source.splitlines() if not line.lstrip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def apply_schema() -> bool:
    connection = MySQLStore("schema-migration")._connect()
    try:
        with connection.cursor() as cursor:
            statements = _statements(SCHEMA_FILE.read_text(encoding="utf-8"))
            cursor.execute(statements[0])  # schema_migrations exists before version lookup
            cursor.execute("SELECT version FROM schema_migrations WHERE version=%s", (SCHEMA_VERSION,))
            if cursor.fetchone() is not None:
                return False
            for statement in statements[1:]:
                cursor.execute(statement)
            cursor.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (SCHEMA_VERSION,))
        connection.commit()
        return True
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def handler(_event, _context):
    return {"version": SCHEMA_VERSION, "applied": apply_schema()}


if __name__ == "__main__":
    print(handler({}, None))
