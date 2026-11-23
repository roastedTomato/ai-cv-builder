"""User-scoped MySQL persistence for the local application and future cloud API."""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator


_cached_db_secret: tuple[str, dict[str, str]] | None = None


def _database_credentials() -> tuple[str, str]:
    """Read local credentials or the cloud secret without exposing its value."""
    global _cached_db_secret
    secret_arn = os.environ.get("CV_MYSQL_SECRET_ARN")
    if secret_arn:
        if _cached_db_secret is None or _cached_db_secret[0] != secret_arn:
            import boto3
            value = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
            credentials = json.loads(value)
            if not isinstance(credentials, dict) or not credentials.get("username") or not credentials.get("password"):
                raise RuntimeError("MySQL 密钥格式无效")
            _cached_db_secret = (secret_arn, credentials)
        credentials = _cached_db_secret[1]
        return credentials["username"], credentials["password"]
    user = os.environ.get("CV_MYSQL_USER")
    if not user:
        raise RuntimeError("MySQL 用户未配置")
    return user, os.environ.get("CV_MYSQL_PASSWORD", "")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class MySQLStore:
    """All queries are constrained by the identity supplied by the trusted API."""

    def __init__(self, user_id: str):
        if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 128:
            raise ValueError("用户 ID 无效")
        self.user_id = user_id

    def _connect(self):
        import pymysql
        database = os.environ.get("CV_MYSQL_DATABASE")
        if not database:
            raise RuntimeError("MySQL 数据库名称未配置")
        user, password = _database_credentials()
        options: dict[str, Any] = {
            "database": database, "user": user,
            "password": password,
            "charset": "utf8mb4", "autocommit": False,
            "cursorclass": pymysql.cursors.DictCursor,
            "connect_timeout": 5, "read_timeout": 20, "write_timeout": 20,
        }
        socket = os.environ.get("CV_MYSQL_SOCKET")
        if socket:
            options["unix_socket"] = socket
        else:
            options["host"] = os.environ.get("CV_MYSQL_HOST", "127.0.0.1")
            options["port"] = int(os.environ.get("CV_MYSQL_PORT", "3306"))
        connection = pymysql.connect(**options)
        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _required(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label}不能为空")
        return value.strip()

    @staticmethod
    def _version(row: dict[str, Any]) -> dict[str, Any]:
        result = {
            "id": row["id"], "number": int(row["number"]),
            "created_at": _stamp(row["created_at"]),
            "job_text": row["job_text"],
            "resume_content": _decode(row["resume_content"]),
            "module_order": _decode(row["module_order"]),
            "visual_settings": _decode(row["visual_settings"]),
        }
        if row["source_version_id"] is not None:
            result["source_version_id"] = row["source_version_id"]
            result["source_version_number"] = int(row["source_version_number"])
        return result

    def load_resume(self) -> dict[str, Any] | None:
        with self._transaction() as cursor:
            cursor.execute("SELECT content FROM base_resumes WHERE user_id=%s", (self.user_id,))
            row = cursor.fetchone()
        return _decode(row["content"]) if row else None

    def save_resume(self, resume: dict[str, Any]) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO base_resumes (user_id,content) VALUES (%s,%s) "
                "ON DUPLICATE KEY UPDATE content=VALUES(content),updated_at=CURRENT_TIMESTAMP(6)",
                (self.user_id, _json(resume)),
            )

    def delete_resume(self) -> None:
        with self._transaction() as cursor:
            cursor.execute("DELETE FROM base_resumes WHERE user_id=%s", (self.user_id,))

    def reset(self) -> None:
        with self._transaction() as cursor:
            # Cascades remove jobs, versions, interviews and turns.
            for table in ("tasks", "companies", "base_resumes"):
                cursor.execute(f"DELETE FROM {table} WHERE user_id=%s", (self.user_id,))

    def reset_job_records(self) -> None:
        with self._transaction() as cursor:
            cursor.execute("DELETE FROM tasks WHERE user_id=%s", (self.user_id,))
            cursor.execute("DELETE FROM companies WHERE user_id=%s", (self.user_id,))

    def list_applications(self) -> dict[str, Any]:
        with self._transaction() as cursor:
            cursor.execute("SELECT id,name FROM companies WHERE user_id=%s ORDER BY created_at,id", (self.user_id,))
            companies = [{"id": row["id"], "name": row["name"], "jobs": []} for row in cursor.fetchall()]
            cursor.execute(
                "SELECT id,company_id,title,job_text,revision,version_counter FROM jobs "
                "WHERE user_id=%s ORDER BY created_at,id", (self.user_id,)
            )
            jobs = cursor.fetchall()
            cursor.execute(
                "SELECT id,job_id,number,source_version_id,source_version_number,job_text,"
                "resume_content,module_order,visual_settings,created_at FROM resume_versions "
                "WHERE user_id=%s ORDER BY job_id,number", (self.user_id,)
            )
            versions = cursor.fetchall()
        company_by_id = {row["id"]: row for row in companies}
        job_by_id = {}
        for row in jobs:
            job = {
                "id": row["id"], "title": row["title"], "job_text": row["job_text"],
                "revision": int(row["revision"]), "version_counter": int(row["version_counter"]),
                "has_generated": int(row["version_counter"]) > 0, "versions": [],
            }
            company_by_id[row["company_id"]]["jobs"].append(job)
            job_by_id[row["id"]] = job
        for row in versions:
            job_by_id[row["job_id"]]["versions"].append(self._version(row))
        return {"companies": companies}

    def load(self) -> dict[str, Any]:
        return self.list_applications()

    def list(self) -> dict[str, Any]:
        return self.list_applications()

    def add_company(self, name: str) -> dict[str, Any]:
        company = {"id": uuid.uuid4().hex, "name": self._required(name, "公司名称"), "jobs": []}
        with self._transaction() as cursor:
            cursor.execute("INSERT INTO companies (user_id,id,name) VALUES (%s,%s,%s)",
                           (self.user_id, company["id"], company["name"]))
        return company

    def rename_company(self, company_id: str, name: str) -> dict[str, Any]:
        name = self._required(name, "公司名称")
        with self._transaction() as cursor:
            cursor.execute("SELECT id FROM companies WHERE user_id=%s AND id=%s FOR UPDATE",
                           (self.user_id, company_id))
            if cursor.fetchone() is None:
                raise KeyError(company_id)
            cursor.execute("UPDATE companies SET name=%s WHERE user_id=%s AND id=%s",
                           (name, self.user_id, company_id))
        return {"id": company_id, "name": name, "jobs": []}

    def delete_company(self, company_id: str) -> None:
        with self._transaction() as cursor:
            cursor.execute("DELETE FROM companies WHERE user_id=%s AND id=%s", (self.user_id, company_id))
            if cursor.rowcount != 1:
                raise KeyError(company_id)

    def add_job(self, company_id: str, title: str, job_text: str) -> dict[str, Any]:
        title, job_text = self._required(title, "职位名称"), self._required(job_text, "招聘要求")
        job = {"id": uuid.uuid4().hex, "title": title, "job_text": job_text,
               "revision": 0, "version_counter": 0, "has_generated": False, "versions": []}
        with self._transaction() as cursor:
            cursor.execute("SELECT id FROM companies WHERE user_id=%s AND id=%s FOR UPDATE",
                           (self.user_id, company_id))
            if cursor.fetchone() is None:
                raise KeyError(company_id)
            cursor.execute(
                "INSERT INTO jobs (user_id,id,company_id,title,job_text) VALUES (%s,%s,%s,%s,%s)",
                (self.user_id, job["id"], company_id, title, job_text)
            )
        return job

    def get_job(self, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT j.id,j.company_id,j.title,j.job_text,j.revision,j.version_counter,"
                "c.name AS company_name FROM jobs j JOIN companies c "
                "ON c.user_id=j.user_id AND c.id=j.company_id "
                "WHERE j.user_id=%s AND j.id=%s", (self.user_id, job_id)
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(job_id)
            cursor.execute(
                "SELECT id,number,source_version_id,source_version_number,job_text,"
                "resume_content,module_order,visual_settings,created_at FROM resume_versions "
                "WHERE user_id=%s AND job_id=%s ORDER BY number", (self.user_id, job_id)
            )
            versions = [self._version(item) for item in cursor.fetchall()]
        company = {"id": row["company_id"], "name": row["company_name"], "jobs": []}
        job = {"id": row["id"], "title": row["title"], "job_text": row["job_text"],
               "revision": int(row["revision"]), "version_counter": int(row["version_counter"]),
               "has_generated": int(row["version_counter"]) > 0, "versions": versions}
        return company, job

    def update_job(self, job_id: str, title: str, job_text: str, expected_revision: int) -> dict[str, Any]:
        title, job_text = self._required(title, "职位名称"), self._required(job_text, "招聘要求")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("职位修订号无效")
        with self._transaction() as cursor:
            cursor.execute(
                "UPDATE jobs SET title=%s,job_text=%s,revision=revision+1 "
                "WHERE user_id=%s AND id=%s AND revision=%s",
                (title, job_text, self.user_id, job_id, expected_revision)
            )
            if cursor.rowcount != 1:
                cursor.execute("SELECT revision FROM jobs WHERE user_id=%s AND id=%s",
                               (self.user_id, job_id))
                if cursor.fetchone() is None:
                    raise KeyError(job_id)
                raise ValueError("职位已被其他操作修改，请刷新后重试")
        return self.get_job(job_id)[1]

    def rename_job(self, job_id: str, title: str) -> dict[str, Any]:
        _, job = self.get_job(job_id)
        return self.update_job(job_id, title, job["job_text"], job["revision"])

    def delete_job(self, job_id: str) -> None:
        with self._transaction() as cursor:
            cursor.execute("DELETE FROM jobs WHERE user_id=%s AND id=%s", (self.user_id, job_id))
            if cursor.rowcount != 1:
                raise KeyError(job_id)

    def delete_version(self, job_id: str, version_id: str) -> None:
        with self._transaction() as cursor:
            cursor.execute(
                "DELETE FROM resume_versions WHERE user_id=%s AND job_id=%s AND id=%s",
                (self.user_id, job_id, version_id)
            )
            if cursor.rowcount != 1:
                raise KeyError(version_id)

    def create_version(
        self, company_id: str, job_id: str, resume_content: Any,
        module_order: list[str], visual_settings: dict[str, Any],
        job_text: str | None = None, *, initial_only: bool = True,
        source_version_id: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT company_id,job_text,revision,version_counter FROM jobs "
                "WHERE user_id=%s AND id=%s FOR UPDATE", (self.user_id, job_id)
            )
            job = cursor.fetchone()
            if job is None or job["company_id"] != company_id:
                raise KeyError(job_id)
            if initial_only and int(job["version_counter"]) != 0:
                raise ValueError("该岗位已生成 V1；请编辑现有版本创建新版。")
            if job_text is not None and job_text != job["job_text"]:
                raise ValueError("职位已被其他操作修改，请刷新后重试")
            source = None
            if source_version_id is not None:
                cursor.execute(
                    "SELECT number FROM resume_versions WHERE user_id=%s AND job_id=%s AND id=%s",
                    (self.user_id, job_id, source_version_id)
                )
                source = cursor.fetchone()
                if source is None:
                    raise KeyError(source_version_id)
            number = int(job["version_counter"]) + 1
            version_id, created = uuid.uuid4().hex, _now()
            cursor.execute(
                "INSERT INTO resume_versions "
                "(user_id,id,job_id,number,source_version_id,source_version_number,job_text,"
                "resume_content,module_order,visual_settings,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (self.user_id, version_id, job_id, number, source_version_id,
                 int(source["number"]) if source else None, job["job_text"],
                 _json(resume_content), _json(module_order), _json(visual_settings), created)
            )
            cursor.execute(
                "UPDATE jobs SET version_counter=%s WHERE user_id=%s AND id=%s",
                (number, self.user_id, job_id)
            )
        result = {"id": version_id, "number": number, "created_at": _stamp(created),
                  "job_text": job["job_text"], "resume_content": resume_content,
                  "module_order": list(module_order), "visual_settings": dict(visual_settings)}
        if source_version_id is not None:
            result.update(source_version_id=source_version_id, source_version_number=int(source["number"]))
        return result

    def save_edited_version(
        self, job_id: str, source_version_id: str, resume_content: Any,
        module_order: list[str], visual_settings: dict[str, Any],
    ) -> dict[str, Any]:
        company, _ = self.get_job(job_id)
        return self.create_version(company["id"], job_id, resume_content, module_order,
                                   visual_settings, initial_only=False,
                                   source_version_id=source_version_id)

    def update_version(self, job_id: str, version_id: str, resume_content: Any,
                       module_order: list[str], visual_settings: dict[str, Any]) -> dict[str, Any]:
        return self.save_edited_version(job_id, version_id, resume_content, module_order, visual_settings)

    @staticmethod
    def _check_interview(session: dict[str, Any]) -> None:
        if not isinstance(session.get("turns"), list) or len(session["turns"]) > 12:
            raise ValueError("面试最多保存 12 道题")
        for value in (
            {key: value for key, value in session.items() if key not in {"resume_content", "job_text"}},
            {"resume_content": session["resume_content"]}, {"job_text": session["job_text"]},
        ):
            if len(_json(value).encode("utf-8")) > 350 * 1024:
                raise ValueError("面试记录过大，单条不能超过 350 KB")

    def create_interview(self, job_id: str, version_id: str) -> dict[str, Any]:
        with self._transaction() as cursor:
            cursor.execute(
                "SELECT j.company_id,j.title,c.name AS company_name FROM jobs j "
                "JOIN companies c ON c.user_id=j.user_id AND c.id=j.company_id "
                "WHERE j.user_id=%s AND j.id=%s FOR UPDATE", (self.user_id, job_id)
            )
            job = cursor.fetchone()
            if job is None:
                raise KeyError(job_id)
            cursor.execute(
                "SELECT number,job_text,resume_content FROM resume_versions "
                "WHERE user_id=%s AND job_id=%s AND id=%s",
                (self.user_id, job_id, version_id)
            )
            version = cursor.fetchone()
            if version is None:
                raise KeyError(version_id)
            now, session_id = _now(), uuid.uuid4().hex
            session = {
                "id": session_id, "company_id": job["company_id"],
                "company_name": job["company_name"], "job_id": job_id,
                "job_title": job["title"], "version_id": version_id,
                "version_number": int(version["number"]), "job_text": version["job_text"],
                "resume_content": _decode(version["resume_content"]),
                "status": "new", "turns": [], "created_at": _stamp(now),
                "updated_at": _stamp(now), "revision": 0, "error": None,
                "version_deleted": False,
            }
            self._check_interview(session)
            cursor.execute("DELETE FROM interviews WHERE user_id=%s AND job_id=%s",
                           (self.user_id, job_id))
            cursor.execute(
                "INSERT INTO interviews "
                "(user_id,id,company_id,company_name,job_id,job_title,version_id,version_number,"
                "job_text,resume_content,status,revision,error,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (self.user_id, session_id, job["company_id"], job["company_name"],
                 job_id, job["title"], version_id, int(version["number"]),
                 version["job_text"], version["resume_content"], "new", 0, None, now, now)
            )
        return session

    def _interview(self, cursor, session_id: str, *, lock: bool = False) -> dict[str, Any] | None:
        cursor.execute(
            "SELECT * FROM interviews WHERE user_id=%s AND id=%s" + (" FOR UPDATE" if lock else ""),
            (self.user_id, session_id)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        cursor.execute(
            "SELECT id FROM resume_versions WHERE user_id=%s AND job_id=%s AND id=%s",
            (self.user_id, row["job_id"], row["version_id"])
        )
        version_exists = cursor.fetchone() is not None
        cursor.execute(
            "SELECT id,question,category,reference_answer,answer,answered_at,feedback,follow_up,created_at "
            "FROM interview_turns WHERE user_id=%s AND interview_id=%s ORDER BY ordinal",
            (self.user_id, session_id)
        )
        turns = []
        for turn in cursor.fetchall():
            saved_turn = {
                "id": turn["id"], "question": turn["question"], "category": turn["category"],
                "reference_answer": turn["reference_answer"], "answer": turn["answer"],
                "feedback": _decode(turn["feedback"]), "follow_up": turn["follow_up"],
                "created_at": _stamp(turn["created_at"]),
            }
            if turn["answered_at"]:
                saved_turn["answered_at"] = _stamp(turn["answered_at"])
            turns.append(saved_turn)
        return {
            "id": row["id"], "company_id": row["company_id"],
            "company_name": row["company_name"], "job_id": row["job_id"],
            "job_title": row["job_title"], "version_id": row["version_id"],
            "version_number": int(row["version_number"]), "job_text": row["job_text"],
            "resume_content": _decode(row["resume_content"]), "status": row["status"],
            "turns": turns, "created_at": _stamp(row["created_at"]),
            "updated_at": _stamp(row["updated_at"]), "revision": int(row["revision"]),
            "error": _decode(row["error"]), "version_deleted": not version_exists,
        }

    def get_interview(self, session_id: str) -> dict[str, Any] | None:
        with self._transaction() as cursor:
            return self._interview(cursor, session_id)

    def list_interviews(self, job_id: str | None = None) -> list[dict[str, Any]]:
        with self._transaction() as cursor:
            query = (
                "SELECT i.id FROM interviews i WHERE i.user_id=%s"
                + (" AND i.job_id=%s" if job_id is not None else "")
                + " ORDER BY i.created_at DESC,i.id DESC"
            )
            cursor.execute(query, (self.user_id, job_id) if job_id is not None else (self.user_id,))
            ids = [row["id"] for row in cursor.fetchall()]
            fields = ("id", "status", "company_id", "company_name", "job_id", "job_title",
                      "version_id", "version_number", "created_at", "updated_at", "version_deleted")
            return [{key: session[key] for key in fields}
                    for session_id in ids if (session := self._interview(cursor, session_id))]

    def update_interview(self, session_id: str, patch: dict[str, Any],
                         expected_status: str | None = None) -> dict[str, Any]:
        if not isinstance(patch, dict) or not patch or set(patch) - {"status", "turns", "error"}:
            raise ValueError("面试更新字段无效")
        if "error" in patch and patch["error"] is not None and not isinstance(patch["error"], dict):
            raise ValueError("面试错误信息无效")
        with self._transaction() as cursor:
            existing = self._interview(cursor, session_id, lock=True)
            if existing is None:
                raise KeyError(session_id)
            if expected_status is not None and existing["status"] != expected_status:
                raise ValueError("面试状态已更新，请刷新后重试")
            updated = {**existing, **patch, "revision": existing["revision"] + 1,
                       "updated_at": _stamp(_now())}
            if not isinstance(updated["status"], str) or not updated["status"]:
                raise ValueError("面试状态无效")
            self._check_interview(updated)
            cursor.execute(
                "UPDATE interviews SET status=%s,revision=%s,error=%s,updated_at=%s "
                "WHERE user_id=%s AND id=%s",
                (updated["status"], updated["revision"],
                 _json(updated["error"]) if updated["error"] is not None else None,
                 datetime.fromisoformat(updated["updated_at"]).replace(tzinfo=None),
                 self.user_id, session_id)
            )
            if "turns" in patch:
                cursor.execute("DELETE FROM interview_turns WHERE user_id=%s AND interview_id=%s",
                               (self.user_id, session_id))
                for ordinal, turn in enumerate(updated["turns"], 1):
                    cursor.execute(
                        "INSERT INTO interview_turns "
                        "(user_id,interview_id,ordinal,id,question,category,reference_answer,"
                        "answer,answered_at,feedback,follow_up,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (self.user_id, session_id, ordinal, turn.get("id") or uuid.uuid4().hex,
                         turn.get("question") or "", turn.get("category"),
                         turn.get("reference_answer"), turn.get("answer"),
                         datetime.fromisoformat(turn["answered_at"]).replace(tzinfo=None)
                         if turn.get("answered_at") else None,
                         _json(turn["feedback"]) if turn.get("feedback") is not None else None,
                         turn.get("follow_up"),
                         datetime.fromisoformat(turn["created_at"]).replace(tzinfo=None)
                         if turn.get("created_at") else _now())
                    )
            return self._interview(cursor, session_id)

    def create_task(self, task: dict[str, Any]) -> None:
        if not isinstance(task, dict) or not task.get("id") or not task.get("kind") or not task.get("status"):
            raise ValueError("任务数据无效")
        if task.get("user_id") not in (None, self.user_id):
            raise ValueError("任务用户不匹配")
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO tasks (user_id,id,kind,status,input_data,result_data,error) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (self.user_id, task["id"], task["kind"], task["status"],
                 _json(task.get("input") or {}), _json(task["result"]) if task.get("result") is not None else None,
                 _json(task["error"]) if task.get("error") is not None else None)
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._transaction() as cursor:
            cursor.execute("SELECT * FROM tasks WHERE user_id=%s AND id=%s", (self.user_id, task_id))
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row["id"], "user_id": self.user_id, "kind": row["kind"],
            "status": row["status"], "input": _decode(row["input_data"]),
            "result": _decode(row["result_data"]), "error": _decode(row["error"]),
            "created_at": _stamp(row["created_at"]), "updated_at": _stamp(row["updated_at"]),
        }

    def update_task(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if any(key in patch for key in ("id", "user_id", "kind", "created_at")):
            raise ValueError("不可修改任务身份字段")
        with self._transaction() as cursor:
            cursor.execute("SELECT status FROM tasks WHERE user_id=%s AND id=%s FOR UPDATE",
                           (self.user_id, task_id))
            row = cursor.fetchone()
            if row is None:
                raise KeyError(task_id)
            if patch.get("status") == "running" and row["status"] not in {"queued", "pending"}:
                raise ValueError("任务已被领取")
            fields, params = [], []
            for key, column in (("status", "status"), ("input", "input_data"),
                                ("result", "result_data"), ("error", "error")):
                if key in patch:
                    fields.append(f"{column}=%s")
                    params.append(_json(patch[key]) if key != "status" and patch[key] is not None else patch[key])
            if fields:
                cursor.execute(
                    "UPDATE tasks SET " + ",".join(fields) + ",updated_at=CURRENT_TIMESTAMP(6) "
                    "WHERE user_id=%s AND id=%s", (*params, self.user_id, task_id)
                )
        result = self.get_task(task_id)
        assert result is not None
        return result


class MySQLResumeStore(MySQLStore):
    """The original local resume interface, backed by the shared MySQL store."""

    def __init__(self, user_id: str = "local-user"):
        super().__init__(user_id)

    def load(self) -> dict[str, Any] | None:
        return self.load_resume()

    def save(self, resume: dict[str, Any]) -> None:
        self.save_resume(resume)

    def delete(self) -> None:
        self.delete_resume()

    def reset(self) -> None:
        self.delete_resume()


class MySQLJobStore(MySQLStore):
    """The original local application-record interface."""

    def __init__(self, user_id: str = "local-user"):
        super().__init__(user_id)

    def reset(self) -> None:
        self.reset_job_records()
