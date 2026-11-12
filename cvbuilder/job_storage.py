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
        return state

    def list(self) -> dict[str, Any]:
        """Return all companies and nested job/version records."""
        return self.load()

    def add_company(self, name: str) -> dict[str, Any]:
        name = self._required_text(name, "公司名称")
        state = self.load()
        company = {"id": uuid.uuid4().hex, "name": name, "jobs": []}
        state["companies"].append(company)
        self._save(state)
        return company

    def add_job(self, company_id: str, title: str, job_text: str) -> dict[str, Any]:
        title = self._required_text(title, "职位名称")
        if not isinstance(job_text, str):
            raise ValueError("招聘要求必须是文本")
        state = self.load()
        company = self._company(state, company_id)
        job = {"id": uuid.uuid4().hex, "title": title, "job_text": job_text, "versions": []}
        company["jobs"].append(job)
        self._save(state)
        return job

    def create_version(
        self,
        company_id: str,
        job_id: str,
        resume_content: Any,
        module_order: list[str],
        visual_settings: dict[str, Any],
        job_text: str | None = None,
    ) -> dict[str, Any]:
        state = self.load()
        job = self._job(self._company(state, company_id), job_id)
        number = max((version["number"] for version in job["versions"]), default=0) + 1
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
        self._save(state)
        return version

    def update_version(
        self,
        version_id: str,
        resume_content: Any,
        module_order: list[str],
        visual_settings: dict[str, Any],
    ) -> dict[str, Any]:
        """Replace editable version data without allocating a new version."""
        state = self.load()
        version = self._find_version(state, version_id)
        version["resume_content"] = resume_content
        version["module_order"] = list(module_order)
        version["visual_settings"] = dict(visual_settings)
        self._save(state)
        return version

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
    def _find_version(state: dict[str, Any], version_id: str) -> dict[str, Any]:
        for company in state["companies"]:
            for job in company.get("jobs", []):
                for version in job.get("versions", []):
                    if version.get("id") == version_id:
                        return version
        raise KeyError(f"找不到简历版本：{version_id}")
