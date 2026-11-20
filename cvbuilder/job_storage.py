"""Atomic local storage for job applications and their resume versions.

This store is deliberately separate from :class:`ResumeStore`: job records
are stored in ``job-records.json`` under the same per-user application data
directory and can be reset without changing base-resume behavior.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import data_directory


class JobRecordStore:
    """Persist companies, jobs, and immutable-numbered resume versions."""

    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_directory()
        self.path = self.directory / "job-records.json"

    def load(self) -> dict[str, Any]:
        """Load the complete state, returning an empty state when absent."""
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"companies": []}
        if not isinstance(state, dict) or not isinstance(state.get("companies"), list):
            raise ValueError("求职记录文件格式无效")
        for company in state["companies"]:
            for job in company.get("jobs", []):
                versions = job.get("versions", [])
                job["revision"] = int(job.get("revision", 0))
                job["has_generated"] = bool(job.get("has_generated") or versions)
                job["version_counter"] = max(
                    int(job.get("version_counter", 0)),
                    max((int(version["number"]) for version in versions), default=0),
                )
        return state

    def list(self) -> dict[str, Any]:
        """Return all companies and nested job/version records."""
        return {"companies": self.load()["companies"]}

    def add_company(self, name: str) -> dict[str, Any]:
        name = self._required_text(name, "公司名称")
        state = self.load()
        company = {"id": uuid.uuid4().hex, "name": name, "jobs": []}
        state["companies"].append(company)
        self._save(state)
        return company

    def rename_company(self, company_id: str, name: str) -> dict[str, Any]:
        name = self._required_text(name, "公司名称")
        state = self.load()
        company = self._company(state, company_id)
        company["name"] = name
        self._save(state)
        return company

    def delete_company(self, company_id: str) -> None:
        with self._locked():
            state = self.load()
            company = self._company(state, company_id)
            state["companies"].remove(company)
            state["interviews"] = [item for item in state.get("interviews", []) if item["company_id"] != company_id]
            self._save(state)

    def add_job(self, company_id: str, title: str, job_text: str) -> dict[str, Any]:
        title = self._required_text(title, "职位名称")
        job_text = self._required_text(job_text, "招聘要求")
        state = self.load()
        company = self._company(state, company_id)
        job = {"id": uuid.uuid4().hex, "title": title, "job_text": job_text,
               "versions": [], "has_generated": False, "version_counter": 0, "revision": 0}
        company["jobs"].append(job)
        self._save(state)
        return job

    def rename_job(self, job_id: str, title: str) -> dict[str, Any]:
        title = self._required_text(title, "职位名称")
        with self._locked():
            state = self.load()
            _, job = self._find_job(state, job_id)
            job["title"] = title
            job["revision"] += 1
            self._save(state)
            return job

    def update_job(self, job_id: str, title: str, job_text: str, expected_revision: int) -> dict[str, Any]:
        title = self._required_text(title, "职位名称")
        job_text = self._required_text(job_text, "招聘要求")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("职位修订号无效")
        with self._locked():
            state = self.load()
            _, job = self._find_job(state, job_id)
            if int(job.get("revision", 0)) != expected_revision:
                raise ValueError("职位已被其他操作修改，请刷新后重试")
            job.update(title=title, job_text=job_text, revision=expected_revision + 1)
            self._save(state)
            return job

    def delete_job(self, job_id: str) -> None:
        with self._locked():
            state = self.load()
            company, job = self._find_job(state, job_id)
            company["jobs"].remove(job)
            state["interviews"] = [item for item in state.get("interviews", []) if item["job_id"] != job_id]
            self._save(state)

    def delete_version(self, job_id: str, version_id: str) -> None:
        with self._locked():
            state = self.load()
            _, job = self._find_job(state, job_id)
            version = next((item for item in job["versions"] if item.get("id") == version_id), None)
            if version is None:
                raise KeyError(version_id)
            job["versions"].remove(version)
            self._save(state)

    def create_version(
        self,
        company_id: str,
        job_id: str,
        resume_content: Any,
        module_order: list[str],
        visual_settings: dict[str, Any],
        job_text: str | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            return self._create_version_unlocked(company_id, job_id, resume_content, module_order,
                                                 visual_settings, job_text)

    def _create_version_unlocked(
        self, company_id: str, job_id: str, resume_content: Any,
        module_order: list[str], visual_settings: dict[str, Any], job_text: str | None,
    ) -> dict[str, Any]:
        state = self.load()
        job = self._job(self._company(state, company_id), job_id)
        if job["has_generated"] or job["versions"] or job["version_counter"]:
            raise ValueError("该岗位已生成 V1；请编辑现有版本创建新版。")
        number = 1
        version = {
            "id": uuid.uuid4().hex,
            "number": number,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_text": job_text if job_text is not None else job["job_text"],
            "resume_content": resume_content,
            "module_order": list(module_order),
            "visual_settings": dict(visual_settings),
        }
        job["versions"].append(version)
        job["has_generated"] = True
        job["version_counter"] = number
        self._save(state)
        return version

    def save_edited_version(
        self,
        job_id: str,
        version_id: str,
        resume_content: Any,
        module_order: list[str],
        visual_settings: dict[str, Any],
    ) -> dict[str, Any]:
        """Create the next version from a selected version without overwriting it."""
        with self._locked():
            return self._save_edited_version_unlocked(job_id, version_id, resume_content,
                                                      module_order, visual_settings)

    def _save_edited_version_unlocked(
        self, job_id: str, version_id: str, resume_content: Any,
        module_order: list[str], visual_settings: dict[str, Any],
    ) -> dict[str, Any]:
        state = self.load()
        located = next(((job, version) for company in state["companies"]
                        for job in company.get("jobs", []) if job.get("id") == job_id
                        for version in job.get("versions", []) if version.get("id") == version_id), None)
        if located is None:
            raise KeyError(version_id)
        job, source = located
        version = {
            "id": uuid.uuid4().hex,
            "number": job["version_counter"] + 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_text": job["job_text"],
            "source_version_id": version_id,
            "source_version_number": source["number"],
            "resume_content": resume_content,
            "module_order": list(module_order),
            "visual_settings": dict(visual_settings),
        }
        job["versions"].append(version)
        job["version_counter"] = version["number"]
        self._save(state)
        return version

    def create_interview(self, job_id: str, version_id: str) -> dict[str, Any]:
        with self._locked():
            state = self.load()
            company, job = self._find_job(state, job_id)
            version = next((v for v in job["versions"] if v["id"] == version_id), None)
            if version is None:
                raise KeyError(version_id)
            now = datetime.now(timezone.utc).isoformat()
            session = {
                "id": uuid.uuid4().hex, "company_id": company["id"], "company_name": company["name"],
                "job_id": job_id, "job_title": job["title"], "version_id": version_id,
                "version_number": version["number"], "job_text": version["job_text"],
                "resume_content": version["resume_content"], "status": "new", "turns": [],
                "created_at": now, "updated_at": now, "revision": 0, "error": None,
            }
            self._check_interview_size(session)
            state["interviews"] = [item for item in state.get("interviews", [])
                                   if item["job_id"] != job_id]
            state["interviews"].append(session)
            self._save(state)
            return {**session, "version_deleted": False}

    def get_interview(self, session_id: str) -> dict[str, Any] | None:
        state = self.load()
        session = next((s for s in state.get("interviews", []) if s["id"] == session_id), None)
        return self._view_interview(state, session) if session else None

    def list_interviews(self, job_id: str | None = None) -> list[dict[str, Any]]:
        state = self.load()
        fields = ("id", "status", "company_id", "company_name", "job_id", "job_title",
                  "version_id", "version_number", "created_at", "updated_at", "version_deleted")
        rows = [{key: view[key] for key in fields}
                for item in state.get("interviews", []) if job_id is None or item["job_id"] == job_id
                for view in [self._view_interview(state, item)]]
        rows.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest.setdefault(row["job_id"], row)
        return list(latest.values())

    def update_interview(self, session_id: str, patch: dict[str, Any], expected_status: str | None = None) -> dict[str, Any]:
        if not isinstance(patch, dict) or not patch or set(patch) - {"status", "turns", "error"}:
            raise ValueError("面试更新字段无效")
        if "error" in patch and patch["error"] is not None and not isinstance(patch["error"], dict):
            raise ValueError("面试错误信息无效")
        with self._locked():
            state = self.load()
            session = next((s for s in state.get("interviews", []) if s["id"] == session_id), None)
            if session is None:
                raise KeyError(session_id)
            if expected_status is not None and session["status"] != expected_status:
                raise ValueError("面试状态已更新，请刷新后重试")
            updated = {**session, **patch, "revision": int(session.get("revision", 0)) + 1,
                       "updated_at": datetime.now(timezone.utc).isoformat()}
            if not isinstance(updated["status"], str) or not updated["status"]:
                raise ValueError("面试状态无效")
            if not isinstance(updated["turns"], list) or len(updated["turns"]) > 12:
                raise ValueError("面试最多保存 12 道题")
            self._check_interview_size(updated)
            state["interviews"][state["interviews"].index(session)] = updated
            self._save(state)
            return self._view_interview(state, updated)

    @staticmethod
    def _check_interview_size(session: dict[str, Any]) -> None:
        parts = [
            {key: value for key, value in session.items() if key not in {"resume_content", "job_text"}},
            {"resume_content": session.get("resume_content")},
            {"job_text": session.get("job_text")},
        ]
        if any(len(json.dumps(part, ensure_ascii=False, default=str).encode("utf-8")) > 350 * 1024
               for part in parts):
            raise ValueError("面试记录过大，单条不能超过 350 KB")

    @staticmethod
    def _view_interview(state: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
        exists = any(version.get("id") == session["version_id"]
                     for company in state["companies"] for job in company.get("jobs", [])
                     if job.get("id") == session["job_id"] for version in job.get("versions", []))
        return {**session, "version_deleted": not exists}

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "job-records.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def reset(self) -> None:
        """Remove job records while leaving the independent base resume intact."""
        self.path.unlink(missing_ok=True)

    def _save(self, state: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _required_text(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label}不能为空")
        return value.strip()

    @staticmethod
    def _company(state: dict[str, Any], company_id: str) -> dict[str, Any]:
        for company in state["companies"]:
            if company.get("id") == company_id:
                return company
        raise KeyError(f"找不到公司：{company_id}")

    @staticmethod
    def _job(company: dict[str, Any], job_id: str) -> dict[str, Any]:
        for job in company.get("jobs", []):
            if job.get("id") == job_id:
                return job
        raise KeyError(f"找不到岗位：{job_id}")

    @staticmethod
    def _find_job(state: dict[str, Any], job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for company in state["companies"]:
            for job in company.get("jobs", []):
                if job.get("id") == job_id:
                    return company, job
        raise KeyError(f"找不到岗位：{job_id}")

    @staticmethod
    def _find_version(state: dict[str, Any], version_id: str) -> dict[str, Any]:
        for company in state["companies"]:
            for job in company.get("jobs", []):
                for version in job.get("versions", []):
                    if version.get("id") == version_id:
                        return version
        raise KeyError(f"找不到简历版本：{version_id}")
