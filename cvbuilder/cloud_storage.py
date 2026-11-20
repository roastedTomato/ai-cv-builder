"""User-scoped DynamoDB storage for the cloud test environment.

The table has string ``pk`` and ``sk`` keys. Every operation supplies the
authenticated Cognito subject in the partition key; caller-supplied IDs are
never used to select another partition.
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_item_size(item: dict[str, Any]) -> None:
    """Reject obviously oversized records before DynamoDB's 400 KiB hard limit."""
    # DynamoDB resource reads numbers as Decimal. String encoding slightly
    # overestimates their JSON byte size, which is safe for this limit.
    size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    if size > 350 * 1024:
        raise ValueError("数据过大，单条云端记录不能超过 350 KB，请缩短内容后重试。")


class CloudStore:
    def __init__(self, user_id: str, table_name: str | None = None, dynamodb_resource=None):
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("用户 ID 不能为空")
        name = table_name or os.environ.get("CV_TABLE")
        if not name:
            raise ValueError("CV_TABLE 未配置")
        if dynamodb_resource is None:
            import boto3
            dynamodb_resource = boto3.resource("dynamodb")
        self.table = dynamodb_resource.Table(name)
        self.user_id = user_id
        self.pk = "USER#" + user_id

    def _key(self, sk: str) -> dict[str, str]:
        return {"pk": self.pk, "sk": sk}

    def _get(self, sk: str) -> dict[str, Any] | None:
        return self.table.get_item(Key=self._key(sk), ConsistentRead=True).get("Item")

    def _put_new(self, sk: str, value: dict[str, Any]) -> None:
        from boto3.dynamodb.conditions import Attr
        self.table.put_item(Item={**self._key(sk), **value}, ConditionExpression=Attr("pk").not_exists())

    def _all(self, prefix: str | None = None) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key
        condition = Key("pk").eq(self.pk)
        if prefix is not None:
            condition &= Key("sk").begins_with(prefix)
        items: list[dict[str, Any]] = []
        start = None
        while True:
            args: dict[str, Any] = {"KeyConditionExpression": condition, "ConsistentRead": True}
            if start is not None:
                args["ExclusiveStartKey"] = start
            response = self.table.query(**args)
            items.extend(response.get("Items", []))
            start = response.get("LastEvaluatedKey")
            if not start:
                return items

    @staticmethod
    def _record(item: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if key not in {"pk", "sk", "revision", "version_counter", "company_id", "job_id"}}

    def load_resume(self) -> dict[str, Any] | None:
        item = self._get("RESUME")
        return item.get("value") if item else None

    def save_resume(self, resume: dict[str, Any]) -> None:
        item = {**self._key("RESUME"), "value": resume}
        _check_item_size(item)
        self.table.put_item(Item=item)

    def delete_resume(self) -> None:
        self.table.delete_item(Key=self._key("RESUME"))

    def reset(self) -> None:
        with self.table.batch_writer() as batch:
            for item in self._all():
                batch.delete_item(Key=self._key(item["sk"]))

    def list_applications(self) -> dict[str, Any]:
        items = self._all()
        companies = []
        company_by_id = {}
        job_by_id = {}
        for item in items:
            if item["sk"].startswith("COMPANY#") and not item.get("deleted"):
                company = {**self._record(item), "jobs": []}
                companies.append(company)
                company_by_id[company["id"]] = company
        for item in items:
            if item["sk"].startswith("JOB#") and not item.get("deleted"):
                company = company_by_id.get(item["company_id"])
                if company is not None:
                    job = {**self._record(item), "versions": [],
                           "has_generated": int(item.get("version_counter", 0)) > 0,
                           "revision": int(item.get("revision", 0))}
                    company["jobs"].append(job)
                    job_by_id[job["id"]] = job
        for item in items:
            if item["sk"].startswith("VERSION#"):
                job = job_by_id.get(item["job_id"])
                if job is not None:
                    job["versions"].append(self._record(item))
        for company in companies:
            company["jobs"].sort(key=lambda row: row["id"])
            for job in company["jobs"]:
                job["versions"].sort(key=lambda row: row["number"])
                job["has_generated"] = job["has_generated"] or bool(job["versions"])
        companies.sort(key=lambda row: row["id"])
        return {"companies": companies}

    @staticmethod
    def _required_text(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label}不能为空")
        return value.strip()

    def add_company(self, name: str) -> dict[str, Any]:
        company = {"id": uuid.uuid4().hex, "name": self._required_text(name, "公司名称")}
        self._put_new("COMPANY#" + company["id"], company)
        return {**company, "jobs": []}

    def rename_company(self, company_id: str, name: str) -> dict[str, Any]:
        from boto3.dynamodb.conditions import Attr
        name = self._required_text(name, "公司名称")
        try:
            self.table.update_item(Key=self._key("COMPANY#" + company_id),
                                   UpdateExpression="SET #name = :name",
                                   ExpressionAttributeNames={"#name": "name"},
                                   ExpressionAttributeValues={":name": name},
                                   ConditionExpression=Attr("pk").exists() & Attr("deleted").not_exists())
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise KeyError(company_id) from exc
        return {**self._record(self._get("COMPANY#" + company_id)), "jobs": []}

    def delete_company(self, company_id: str) -> None:
        self._mark_deleted("COMPANY#" + company_id, company_id)
        # The tombstone blocks new jobs while strongly consistent reads find
        # children already committed before it.
        for job in self._all("JOB#"):
            if job.get("company_id") == company_id:
                self._delete_job_item(job["id"])
        self.table.delete_item(Key=self._key("COMPANY#" + company_id))

    def add_job(self, company_id: str, title: str, job_text: str) -> dict[str, Any]:
        company = self._get("COMPANY#" + company_id)
        if company is None or company.get("deleted"):
            raise KeyError(company_id)
        job_text = self._required_text(job_text, "招聘要求")
        job = {"id": uuid.uuid4().hex, "company_id": company_id, "title": self._required_text(title, "职位名称"), "job_text": job_text, "revision": 0}
        _check_item_size({**self._key("JOB#" + job["id"]), **job})
        from botocore.exceptions import ClientError
        try:
            self.table.meta.client.transact_write_items(TransactItems=[
                {"ConditionCheck": {"TableName": self.table.name,
                                    "Key": self._key("COMPANY#" + company_id),
                                    "ConditionExpression": "attribute_exists(pk) AND attribute_not_exists(deleted)"}},
                {"Put": {"TableName": self.table.name,
                         "Item": {**self._key("JOB#" + job["id"]), **job},
                         "ConditionExpression": "attribute_not_exists(pk)"}},
            ])
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                raise KeyError(company_id) from exc
            raise
        return {"id": job["id"], "title": job["title"], "job_text": job_text,
                "versions": [], "has_generated": False, "revision": 0}

    def update_job(self, job_id: str, title: str, job_text: str, expected_revision: int) -> dict[str, Any]:
        from boto3.dynamodb.conditions import Attr
        title = self._required_text(title, "职位名称")
        job_text = self._required_text(job_text, "招聘要求")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("职位修订号无效")
        self.get_job(job_id)
        existing = self._get("JOB#" + job_id)
        if existing is None or existing.get("deleted"):
            raise KeyError(job_id)
        if int(existing.get("revision", 0)) != expected_revision:
            raise ValueError("职位已被其他操作修改，请刷新后重试")
        updated = {**existing, "title": title, "job_text": job_text, "revision": expected_revision + 1}
        _check_item_size(updated)
        condition = Attr("pk").exists() & Attr("deleted").not_exists()
        condition &= Attr("revision").eq(expected_revision) if "revision" in existing else Attr("revision").not_exists()
        try:
            self.table.put_item(Item=updated, ConditionExpression=condition)
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise ValueError("职位已被其他操作修改，请刷新后重试") from exc
        return self.get_job(job_id)[1]

    def rename_job(self, job_id: str, title: str) -> dict[str, Any]:
        job = self.get_job(job_id)[1]
        return self.update_job(job_id, title, job["job_text"], job["revision"])

    def delete_job(self, job_id: str) -> None:
        self.get_job(job_id)
        self._delete_job_item(job_id)

    def _mark_deleted(self, sk: str, record_id: str) -> None:
        from boto3.dynamodb.conditions import Attr
        try:
            self.table.update_item(Key=self._key(sk),
                                   UpdateExpression="SET deleted = :deleted",
                                   ExpressionAttributeValues={":deleted": True},
                                   ConditionExpression=Attr("pk").exists() & Attr("deleted").not_exists())
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise KeyError(record_id) from exc

    def _delete_job_item(self, job_id: str) -> None:
        job = self._get("JOB#" + job_id)
        if job is None:
            return
        if not job.get("deleted"):
            self._mark_deleted("JOB#" + job_id, job_id)
        with self.table.batch_writer() as batch:
            for version in self._all("VERSION#" + job_id + "#"):
                batch.delete_item(Key=self._key(version["sk"]))
            for interview in self._all("INTERVIEW#"):
                if interview.get("job_id") == job_id:
                    batch.delete_item(Key=self._key(interview["sk"]))
            for snapshot in self._all("INTERVIEW_SNAPSHOT#"):
                if snapshot.get("job_id") == job_id:
                    batch.delete_item(Key=self._key(snapshot["sk"]))
            for context in self._all("INTERVIEW_CONTEXT#"):
                if context.get("job_id") == job_id:
                    batch.delete_item(Key=self._key(context["sk"]))
        self.table.delete_item(Key=self._key("JOB#" + job_id))

    def delete_version(self, job_id: str, version_id: str) -> None:
        from boto3.dynamodb.conditions import Attr
        self.get_job(job_id)
        try:
            self.table.delete_item(Key=self._key("VERSION#" + job_id + "#" + version_id),
                                   ConditionExpression=Attr("pk").exists())
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise KeyError(version_id) from exc

    def get_job(self, job_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        job = self._get("JOB#" + job_id)
        if job is None or job.get("deleted"):
            raise KeyError(job_id)
        company = self._get("COMPANY#" + job["company_id"])
        if company is None or company.get("deleted"):
            raise KeyError(job_id)
        versions = [self._record(item) for item in self._all("VERSION#" + job_id + "#")]
        versions.sort(key=lambda row: row["number"])
        return ({**self._record(company), "jobs": []},
                {**self._record(job), "versions": versions,
                 "revision": int(job.get("revision", 0)),
                 "has_generated": int(job.get("version_counter", 0)) > 0 or bool(versions)})

    def create_version(self, company_id: str, job_id: str, resume_content: Any,
                       module_order: list[str], visual_settings: dict[str, Any],
                       job_text: str | None = None, *, initial_only: bool = False,
                       source_version_id: str | None = None) -> dict[str, Any]:
        """Atomically advance a job counter and persist its numbered version."""
        from botocore.exceptions import ClientError
        # A resource's underlying client marshals native Python values itself.
        client = self.table.meta.client
        for _ in range(20):
            job = self._get("JOB#" + job_id)
            if job is None or job.get("deleted") or job["company_id"] != company_id:
                raise KeyError(job_id)
            if initial_only and int(job.get("version_counter", 0)) != 0:
                raise ValueError("此岗位已有初始版本，请编辑现有版本后保存新版本。")
            source = None
            if source_version_id is not None:
                source = self._get("VERSION#" + job_id + "#" + source_version_id)
                if source is None:
                    raise KeyError(source_version_id)
            number = int(job.get("version_counter", 0)) + 1
            version = {
                "id": uuid.uuid4().hex, "number": number, "created_at": _now(),
                "job_text": job_text if job_text is not None else job["job_text"],
                "resume_content": resume_content, "module_order": list(module_order),
                "visual_settings": dict(visual_settings), "source_version_id": source_version_id,
                "source_version_number": source["number"] if source else None,
            }
            _check_item_size({**self._key("VERSION#" + job_id + "#" + version["id"]),
                              "job_id": job_id, **version})
            revision = int(job.get("revision", 0))
            condition = "attribute_exists(pk) AND attribute_not_exists(deleted) AND company_id = :company AND "
            condition += "(attribute_not_exists(revision) OR revision = :revision)" if revision == 0 else "revision = :revision"
            try:
                actions = [
                    {"Update": {
                        "TableName": self.table.name, "Key": self._key("JOB#" + job_id),
                        "UpdateExpression": "SET version_counter = :number, revision = :next_revision",
                        "ConditionExpression": condition + (" AND attribute_not_exists(version_counter)" if initial_only else ""),
                        "ExpressionAttributeValues": {":company": company_id, ":revision": revision,
                                                      ":number": number, ":next_revision": revision + 1},
                    }},
                    {"Put": {
                        "TableName": self.table.name,
                        "Item": {**self._key("VERSION#" + job_id + "#" + version["id"]),
                                 "job_id": job_id, **version},
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }},
                ]
                if source_version_id is not None:
                    actions.append({"ConditionCheck": {
                        "TableName": self.table.name,
                        "Key": self._key("VERSION#" + job_id + "#" + source_version_id),
                        "ConditionExpression": "attribute_exists(pk)",
                    }})
                client.transact_write_items(TransactItems=actions)
                return version
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                    raise
        raise RuntimeError("版本并发写入冲突，请重试")

    def update_version(self, job_id: str, version_id: str, resume_content: Any,
                       module_order: list[str], visual_settings: dict[str, Any]) -> dict[str, Any]:
        """Compatibility alias that saves a new version without changing its source."""
        return self.save_edited_version(job_id, version_id, resume_content, module_order, visual_settings)

    def save_edited_version(self, job_id: str, source_version_id: str, resume_content: Any,
                            module_order: list[str], visual_settings: dict[str, Any]) -> dict[str, Any]:
        company, _ = self.get_job(job_id)
        return self.create_version(company["id"], job_id, resume_content, module_order,
                                   visual_settings, source_version_id=source_version_id)

    def create_interview(self, job_id: str, version_id: str) -> dict[str, Any]:
        company, job = self.get_job(job_id)
        version = next((v for v in job["versions"] if v["id"] == version_id), None)
        if version is None:
            raise KeyError(version_id)
        now = _now()
        session = {
            "id": uuid.uuid4().hex, "company_id": company["id"], "company_name": company["name"],
            "job_id": job_id, "job_title": job["title"], "version_id": version_id,
            "version_number": version["number"], "status": "new", "turns": [],
            "created_at": now, "updated_at": now, "revision": 0, "error": None,
        }
        snapshot = {"job_id": job_id, "resume_content": version["resume_content"]}
        context = {"job_id": job_id, "job_text": version["job_text"]}
        _check_item_size({**self._key("INTERVIEW_SNAPSHOT#" + session["id"]), **snapshot})
        _check_item_size({**self._key("INTERVIEW_CONTEXT#" + session["id"]), **context})
        self._checked_interview(session)
        self._put_new("INTERVIEW_SNAPSHOT#" + session["id"], snapshot)
        try:
            self._put_new("INTERVIEW_CONTEXT#" + session["id"], context)
            self._put_new("INTERVIEW#" + session["id"], session)
        except Exception:
            self.table.delete_item(Key=self._key("INTERVIEW_SNAPSHOT#" + session["id"]))
            self.table.delete_item(Key=self._key("INTERVIEW_CONTEXT#" + session["id"]))
            raise
        # A deletion that completed while the session was being written must
        # not leave a detached interview behind.
        try:
            current = self.get_job(job_id)[1]
            if not any(v["id"] == version_id for v in current["versions"]):
                raise KeyError(version_id)
        except KeyError:
            self.table.delete_item(Key=self._key("INTERVIEW#" + session["id"]))
            self.table.delete_item(Key=self._key("INTERVIEW_SNAPSHOT#" + session["id"]))
            self.table.delete_item(Key=self._key("INTERVIEW_CONTEXT#" + session["id"]))
            raise
        self._remove_older_interviews(job_id, session["id"], now)
        return {**session, "job_text": version["job_text"],
                "resume_content": version["resume_content"], "version_deleted": False}

    def _remove_older_interviews(self, job_id: str, keep_id: str, created_at: str) -> None:
        """Retain the newest interview for this user's job after a successful start."""
        older = [item for item in self._all("INTERVIEW#")
                 if item.get("job_id") == job_id and item["id"] != keep_id
                 and (item.get("created_at", ""), item["id"]) < (created_at, keep_id)]
        if not older:
            return
        with self.table.batch_writer() as batch:
            for item in older:
                for prefix in ("INTERVIEW#", "INTERVIEW_SNAPSHOT#", "INTERVIEW_CONTEXT#"):
                    batch.delete_item(Key=self._key(prefix + item["id"]))

    def _checked_interview(self, session: dict[str, Any]) -> dict[str, Any]:
        item = {**self._key("INTERVIEW#" + session["id"]), **session}
        _check_item_size(item)
        return session

    def _interview_record(self, item: dict[str, Any]) -> dict[str, Any]:
        session = {key: value for key, value in item.items() if key not in {"pk", "sk"}}
        session["revision"] = int(session.get("revision", 0))
        snapshot = self._get("INTERVIEW_SNAPSHOT#" + session["id"])
        session["resume_content"] = snapshot.get("resume_content") if snapshot else None
        context = self._get("INTERVIEW_CONTEXT#" + session["id"])
        session["job_text"] = context.get("job_text") if context else None
        session["version_deleted"] = self._get("VERSION#" + session["job_id"] + "#" + session["version_id"]) is None
        return session

    def get_interview(self, session_id: str) -> dict[str, Any] | None:
        item = self._get("INTERVIEW#" + session_id)
        return self._interview_record(item) if item else None

    def list_interviews(self, job_id: str | None = None) -> list[dict[str, Any]]:
        fields = ("id", "status", "company_id", "company_name", "job_id", "job_title",
                  "version_id", "version_number", "created_at", "updated_at")
        rows = [{**{key: item[key] for key in fields},
                 "version_deleted": self._get("VERSION#" + item["job_id"] + "#" + item["version_id"]) is None}
                for item in self._all("INTERVIEW#")
                if job_id is None or item.get("job_id") == job_id]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            latest.setdefault(row["job_id"], row)
        return list(latest.values())

    def update_interview(self, session_id: str, patch: dict[str, Any], expected_status: str | None = None) -> dict[str, Any]:
        from boto3.dynamodb.conditions import Attr
        if not isinstance(patch, dict) or not patch or set(patch) - {"status", "turns", "error"}:
            raise ValueError("面试更新字段无效")
        if "error" in patch and patch["error"] is not None and not isinstance(patch["error"], dict):
            raise ValueError("面试错误信息无效")
        for _ in range(20):
            existing = self._get("INTERVIEW#" + session_id)
            if existing is None:
                raise KeyError(session_id)
            if expected_status is not None and existing["status"] != expected_status:
                raise ValueError("面试状态已更新，请刷新后重试")
            revision = int(existing.get("revision", 0))
            updated = {**existing, **patch, "updated_at": _now(), "revision": revision + 1}
            if not isinstance(updated["status"], str) or not updated["status"]:
                raise ValueError("面试状态无效")
            if not isinstance(updated["turns"], list) or len(updated["turns"]) > 12:
                raise ValueError("面试最多保存 12 道题")
            _check_item_size(updated)
            condition = Attr("pk").exists() & Attr("revision").eq(revision)
            if expected_status is not None:
                condition &= Attr("status").eq(expected_status)
            try:
                self.table.put_item(Item=updated, ConditionExpression=condition)
                return self._interview_record(updated)
            except self.table.meta.client.exceptions.ConditionalCheckFailedException:
                if expected_status is not None:
                    raise ValueError("面试状态已更新，请刷新后重试")
        raise ValueError("面试并发写入冲突，请刷新后重试")

    def create_task(self, task: dict[str, Any]) -> None:
        if not isinstance(task, dict) or not task.get("id") or not task.get("kind") or not task.get("status"):
            raise ValueError("任务数据无效")
        if task.get("user_id") not in (None, self.pk[5:]):
            raise ValueError("任务用户不匹配")
        payload = {**task, "user_id": self.user_id, "created_at": task.get("created_at") or _now(),
                   "updated_at": task.get("updated_at") or _now(), "revision": 0}
        _check_item_size({**self._key("TASK#" + task["id"]), **payload})
        self._put_new("TASK#" + task["id"], payload)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        item = self._get("TASK#" + task_id)
        return self._record(item) if item else None

    def update_task(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if any(key in patch for key in ("id", "user_id", "kind", "created_at")):
            raise ValueError("不可修改任务身份字段")
        from boto3.dynamodb.conditions import Attr
        existing = self._get("TASK#" + task_id)
        if existing is None:
            raise KeyError(task_id)
        if patch.get("status") == "running" and existing.get("status") not in {"queued", "pending"}:
            raise ValueError("任务已被领取")
        revision = int(existing.get("revision", 0))
        updated = {**existing, **patch, "updated_at": _now(), "revision": revision + 1}
        condition = Attr("pk").exists() & Attr("revision").eq(revision)
        if patch.get("status") == "running":
            condition &= Attr("status").eq(existing["status"])
        try:
            self.table.put_item(Item={**self._key("TASK#" + task_id), **updated},
                                ConditionExpression=condition)
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise ValueError("任务状态已更新，请重试") from exc
        return self._record(updated)
