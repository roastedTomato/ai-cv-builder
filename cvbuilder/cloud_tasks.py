"""Asynchronous, user-scoped document and AI work for the cloud test site."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from . import ai
from .cli import ROOT
from .importers import ImportErrorBase, extract_text
from .importers.fields import extract_personal, keep_explicit_skills
from .templates import convert_to_pdf, fill_template_modular

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_INLINE_RESULT_BYTES = 300 * 1024
LOGGER = logging.getLogger("cvbuilder.cloud_tasks")
TEMPLATE_IDS = {f"template-{i:02d}" for i in range(1, 4)}
MODULES = {"summary", "education", "work", "projects", "skills", "certificates"}
_cached_secret_arn: str | None = None


class TaskFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskFailure("invalid_task", f"任务缺少{field}。")
    return value.strip()


def _load_openai_key(secrets_client: Any = None) -> None:
    """Resolve the configured secret once per warm worker without logging its value."""
    global _cached_secret_arn
    arn = os.environ.get("CV_OPENAI_SECRET_ARN")
    if not arn or _cached_secret_arn == arn:
        return
    if secrets_client is None:
        import boto3
        secrets_client = boto3.client("secretsmanager")
    try:
        value = secrets_client.get_secret_value(SecretId=arn)["SecretString"]
        if value.lstrip().startswith("{"):
            value = json.loads(value).get("OPENAI_API_KEY", "")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("empty key")
    except Exception as exc:
        raise TaskFailure("ai_configuration", "AI 服务密钥不可用，请检查云端配置。") from exc
    os.environ["OPENAI_API_KEY"] = value
    _cached_secret_arn = arn


def _upload_location(task_id: str, user_id: str, task_input: dict[str, Any]) -> tuple[str, str]:
    key = task_input.get("key")
    if isinstance(key, str):
        match = re.fullmatch(rf"uploads/{re.escape(user_id)}/[a-f0-9]{{32}}(\.docx|\.doc|\.pdf)", key)
        if not match:
            raise TaskFailure("invalid_task", "上传文件无效。")
        return key, match.group(1)
    suffix = task_input.get("suffix")
    if suffix not in {".doc", ".docx", ".pdf"}:
        raise TaskFailure("unsupported_format", "仅支持 .doc、.docx 和 PDF。")
    return f"uploads/{user_id}/{task_id}{suffix}", suffix


def _import_resume(task_id: str, user_id: str, task_input: dict[str, Any], bucket: str, s3: Any) -> dict[str, Any]:
    key, suffix = _upload_location(task_id, user_id, task_input)
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    try:
        content = body.read(MAX_UPLOAD_BYTES + 1)
    finally:
        body.close()
    if len(content) > MAX_UPLOAD_BYTES or response.get("ContentLength", 0) > MAX_UPLOAD_BYTES:
        raise TaskFailure("file_too_large", "文件大小不能超过 15 MB。")
    with tempfile.TemporaryDirectory(prefix="cvbuilder-import-") as folder:
        path = Path(folder) / ("upload" + suffix)
        path.write_bytes(content)
        extracted = extract_text(path)
    personal = extract_personal(extracted.text)
    _load_openai_key()
    draft = ai.structure_resume(extracted.text, personal)
    model_personal = draft.get("personal", {})
    draft["personal"] = {
        key: personal[key] or (model_personal.get(key, "") if isinstance(model_personal, dict) else "")
        for key in ("name", "email", "phone", "location")
    }
    keep_explicit_skills(draft, extracted.text)
    schema = json.loads((ROOT / "docs/schemas/base-resume.schema.json").read_text(encoding="utf-8"))
    if list(Draft202012Validator(schema).iter_errors(draft)):
        raise TaskFailure("invalid_draft", "导入草稿格式无效，请手动填写或重试。")
    return {"draft_id": task_id, "format": extracted.format, "draft": draft}


def _generate_version(store: Any, task_input: dict[str, Any]) -> dict[str, Any]:
    job_id = _required_text(task_input.get("job_id"), "岗位 ID")
    try:
        company, job = store.get_job(job_id)
    except KeyError as exc:
        raise TaskFailure("not_found", "岗位记录不存在。") from exc
    if job.get("has_generated") or job.get("versions"):
        raise TaskFailure("version_exists", "该岗位已有简历版本，请编辑现有版本并保存。")
    resume = store.load_resume()
    if not resume:
        raise TaskFailure("resume_required", "请先保存基础简历。")
    _load_openai_key()
    content = ai.generate_resume_from_job_unchecked(resume, job["job_text"])
    order = [key for key in ("summary", "education", "work", "projects", "skills", "certificates") if content.get(key)]
    try:
        version = store.create_version(
            company["id"], job_id, content, order,
            {"template_id": "template-01", "color": "#2C5263", "page_width": "standard"},
            job["job_text"], initial_only=True,
        )
    except ValueError as exc:
        # A concurrent task may have created V1 while this AI call was running.
        if store.get_job(job_id)[1].get("versions"):
            raise TaskFailure("version_exists", "该岗位已有简历版本，请编辑现有版本并保存。") from exc
        raise
    return {"version": version}


def _generate(store: Any, task_input: dict[str, Any]) -> dict[str, Any]:
    resume = store.load_resume()
    if not resume:
        raise TaskFailure("resume_required", "请先保存基础简历。")
    job_text = _required_text(task_input.get("job_text"), "招聘要求")
    _load_openai_key()
    return {"draft": ai.generate_resume_from_job_unchecked(resume, job_text)}


def _export(task_id: str, user_id: str, store: Any, task_input: dict[str, Any], bucket: str, s3: Any) -> dict[str, Any]:
    job_id = _required_text(task_input.get("job_id"), "岗位 ID")
    version_id = _required_text(task_input.get("version_id"), "版本 ID")
    try:
        _, job = store.get_job(job_id)
        version = next(v for v in job["versions"] if v["id"] == version_id)
    except (KeyError, StopIteration) as exc:
        raise TaskFailure("not_found", "岗位简历版本不存在。") from exc
    template_id = task_input.get("template_id")
    fmt = task_input.get("format")
    if template_id not in TEMPLATE_IDS or fmt not in {"docx", "pdf"}:
        raise TaskFailure("invalid_task", "请选择有效模板和导出格式。")
    content = version["resume_content"]
    order = version["module_order"]
    settings = dict(version["visual_settings"])
    if not isinstance(content, dict) or not isinstance(order, list) or not set(order).issubset(MODULES):
        raise TaskFailure("invalid_task", "简历内容或模块顺序无效。")
    settings["template_id"] = template_id
    with tempfile.TemporaryDirectory(prefix="cvbuilder-export-") as folder:
        path = Path(folder) / "resume.docx"
        fill_template_modular(ROOT / "templates" / f"{template_id}.docx", content, order, settings, path)
        if fmt == "pdf":
            path = convert_to_pdf(path)
        key = f"exports/{user_id}/{task_id}.{fmt}"
        media = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": media})
    return {"key": key, "format": fmt}


def process_task(user_id: str, task_id: str, *, store: Any = None, s3_client: Any = None, bucket: str | None = None) -> dict[str, Any]:
    """Execute one queued task; return its final public status record."""
    if store is None:
        from .cloud_storage import CloudStore
        store = CloudStore(user_id)
    task = store.get_task(task_id)
    if not task:
        raise KeyError(task_id)
    if task.get("status") != "queued":
        return task
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")
    s3 = s3_client
    bucket = bucket or os.environ["CV_FILES_BUCKET"]
    try:
        store.update_task(task_id, {"status": "running"})
    except ValueError:
        # Another Lambda invocation claimed this task after the initial read.
        return store.get_task(task_id)
    task_input = task.get("input") or {}
    try:
        if task["kind"] == "import":
            result = _import_resume(task_id, user_id, task_input, bucket, s3)
        elif task["kind"] == "generate_version":
            result = _generate_version(store, task_input)
        elif task["kind"] == "generate":
            result = _generate(store, task_input)
        elif task["kind"] == "export":
            result = _export(task_id, user_id, store, task_input, bucket, s3)
        else:
            raise TaskFailure("invalid_task", "不支持的后台任务。")
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_INLINE_RESULT_BYTES:
            result_key = f"results/{user_id}/{task_id}.json"
            s3.put_object(Bucket=bucket, Key=result_key, Body=encoded, ContentType="application/json")
            result = {"result_key": result_key}
        return store.update_task(task_id, {"status": "succeeded", "result": result, "error": None})
    except Exception as exc:
        LOGGER.error("Task failed kind=%s type=%s", task.get("kind"), type(exc).__name__)
        if isinstance(exc, TaskFailure):
            error = {"code": exc.code, "message": exc.message}
        elif isinstance(exc, ImportErrorBase):
            error = {"code": exc.code, "message": str(exc)}
        elif isinstance(exc, (ai.ModelConfigurationError, ai.ModelResponseError)):
            error = {"code": "ai_error", "message": "AI 处理失败，请检查服务配置后重试。"}
        else:
            error = {"code": "task_failed", "message": "处理失败，请稍后重试。"}
        return store.update_task(task_id, {"status": "failed", "result": None, "error": error})
    finally:
        if task.get("kind") == "import":
            try:
                key, _ = _upload_location(task_id, user_id, task_input)
            except TaskFailure:
                key = None
            if key:
                try:
                    s3.delete_object(Bucket=bucket, Key=key)
                except Exception as exc:
                    LOGGER.error("Temporary upload cleanup failed type=%s", type(exc).__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return process_task(_required_text(event.get("user_id"), "用户 ID"), _required_text(event.get("task_id"), "任务 ID"))
