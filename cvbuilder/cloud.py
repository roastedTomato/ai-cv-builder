"""Authenticated HTTP API for the cloud test environment.

API Gateway's JWT authorizer validates the Cognito access token. This module
also requires the verified subject in the gateway event before opening storage.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
try:
    from mangum import Mangum
except ImportError:  # Local unit tests can run before the cloud extra is installed.
    Mangum = None

from .mysql_storage import MySQLStore as CloudStore
from .templates import PUBLIC_TEMPLATES, TEMPLATE_IDS
from .web import validate, validate_required_base_fields, validate_editor_payload


app = FastAPI(title="简历生成平台云端 API", docs_url=None, redoc_url=None, openapi_url=None)
MAX_UPLOAD = 15 * 1024 * 1024
SUFFIXES = {"doc", "docx", "pdf"}
KINDS = {"import", "extract_job", "generate_version", "generate", "export", "interview_question", "interview_reference", "interview_feedback"}


@app.options("/api/{path:path}")
def preflight(path: str):
    from fastapi.responses import Response
    return Response(status_code=204)


def aws_client(name: str):
    import boto3
    from botocore.config import Config
    if name == "s3":
        return boto3.client(name, config=Config(signature_version="s3v4"))
    return boto3.client(name)


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "request_error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(Exception)
async def safe_error_handler(_request: Request, _exc: Exception):
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "操作失败，请稍后重试。"}})


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": str(exc)}})


def fail(status: int, code: str, message: str) -> None:
    raise HTTPException(status, detail={"code": code, "message": message})


def user_id(request: Request) -> str:
    event = request.scope.get("aws.event") or {}
    context = event.get("requestContext") or {}
    jwt = (context.get("authorizer") or {}).get("jwt") or {}
    claims = jwt.get("claims") or {}
    subject = claims.get("sub")
    issuer = os.environ.get("CV_COGNITO_ISSUER")
    client = os.environ.get("CV_COGNITO_CLIENT_ID")
    if not subject or claims.get("token_use") != "access" or (issuer and claims.get("iss") != issuer) or (client and claims.get("client_id") != client):
        fail(401, "unauthorized", "请先登录。")
    return str(subject)


def cloud_store(uid: str = Depends(user_id)) -> CloudStore:
    return CloudStore(uid)


def _task(store: CloudStore, kind: str, task_input: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    task = {"id": uuid.uuid4().hex, "kind": kind, "status": "queued", "input": task_input,
            "result": None, "error": None, "created_at": now, "updated_at": now}
    store.create_task(task)
    worker = os.environ.get("CV_WORKER_FUNCTION")
    if not worker:
        fail(503, "worker_unavailable", "后台任务尚未配置。")
    try:
        aws_client("lambda").invoke(FunctionName=worker, InvocationType="Event",
            Payload=__import__("json").dumps({"user_id": store.user_id, "task_id": task["id"]}).encode())
    except Exception:
        store.update_task(task["id"], {"status": "failed", "error": {"code": "dispatch_error", "message": "任务启动失败，请重试。"}})
        fail(503, "dispatch_error", "任务启动失败，请重试。")
    return {"task_id": task["id"], "status": "queued"}


@app.get("/api/templates")
def templates(_store: CloudStore = Depends(cloud_store)):
    return list(PUBLIC_TEMPLATES)


@app.get("/api/resume")
def get_resume(store: CloudStore = Depends(cloud_store)):
    return store.load_resume()


@app.put("/api/resume")
def put_resume(resume: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    validate(resume, "base-resume.schema.json")
    validate_required_base_fields(resume)
    store.save_resume(resume)
    return resume


@app.delete("/api/resume")
def delete_resume(store: CloudStore = Depends(cloud_store)):
    store.delete_resume()
    return {"ok": True}


@app.post("/api/reset")
def reset(store: CloudStore = Depends(cloud_store)):
    store.reset()
    return {"ok": True}


@app.get("/api/applications")
def applications(store: CloudStore = Depends(cloud_store)):
    return store.list_applications()


@app.post("/api/companies")
def add_company(payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        fail(422, "validation_error", "请填写公司名称。")
    return store.add_company(name.strip())


@app.patch("/api/companies/{company_id}")
def rename_company(company_id: str, payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    try:
        return store.rename_company(company_id, payload.get("name"))
    except KeyError:
        fail(404, "not_found", "公司记录不存在。")
    except ValueError as exc:
        fail(422, "validation_error", str(exc))


@app.delete("/api/companies/{company_id}")
def delete_company(company_id: str, store: CloudStore = Depends(cloud_store)):
    try:
        store.delete_company(company_id)
        return {"ok": True}
    except KeyError:
        fail(404, "not_found", "公司记录不存在。")


@app.post("/api/companies/{company_id}/jobs")
def add_job(company_id: str, payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    title, job_text = payload.get("title"), payload.get("job_text")
    if not isinstance(title, str) or not title.strip() or not isinstance(job_text, str) or not job_text.strip():
        fail(422, "validation_error", "请填写职位名称和招聘要求。")
    try:
        return store.add_job(company_id, title.strip(), job_text.strip())
    except KeyError:
        fail(404, "not_found", "公司记录不存在。")


@app.patch("/api/jobs/{job_id}")
def rename_job(job_id: str, payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    try:
        if "job_text" in payload:
            return store.update_job(job_id, payload.get("title"), payload.get("job_text"), payload.get("expected_revision"))
        return store.rename_job(job_id, payload.get("title"))
    except KeyError:
        fail(404, "not_found", "岗位记录不存在。")
    except ValueError as exc:
        conflict = "请刷新后重试" in str(exc) or "并发" in str(exc)
        fail(409 if conflict else 422, "conflict" if conflict else "validation_error", str(exc))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, store: CloudStore = Depends(cloud_store)):
    try:
        store.delete_job(job_id)
        return {"ok": True}
    except KeyError:
        fail(404, "not_found", "岗位记录不存在。")


@app.post("/api/jobs/{job_id}/versions")
def create_version(job_id: str, store: CloudStore = Depends(cloud_store)):
    try:
        _, job = store.get_job(job_id)
    except KeyError:
        fail(404, "not_found", "岗位记录不存在。")
    if job.get("has_generated") or job["versions"]:
        fail(409, "version_exists", "该岗位已生成 V1；请编辑现有版本创建新版。")
    if not store.load_resume():
        fail(409, "resume_required", "请先保存基础简历。")
    return _task(store, "generate_version", {"job_id": job_id, "job_text": job["job_text"]})


@app.post("/api/jobs/{job_id}/versions/{version_id}/edits")
def update_version(job_id: str, version_id: str, payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    content, order, settings = payload.get("resume_content"), payload.get("module_order"), payload.get("visual_settings")
    try:
        validate_editor_payload(content, order, settings)
        return store.save_edited_version(job_id, version_id, content, order, settings)
    except ValueError as exc:
        fail(422, "validation_error", str(exc))
    except KeyError:
        fail(404, "not_found", "岗位简历版本不存在。")


@app.put("/api/jobs/{job_id}/versions/{version_id}/draft")
def save_resume_draft(job_id: str, version_id: str, payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    content, order, settings = payload.get("resume_content"), payload.get("module_order"), payload.get("visual_settings")
    try:
        validate_editor_payload(content, order, settings)
        return store.save_resume_draft(job_id, version_id, content, order, settings)
    except ValueError as exc:
        fail(422, "validation_error", str(exc))
    except KeyError:
        fail(404, "not_found", "岗位简历版本不存在。")


@app.put("/api/jobs/{job_id}/versions/{version_id}")
def overwrite_resume_version(job_id: str, version_id: str, payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    content, order, settings = payload.get("resume_content"), payload.get("module_order"), payload.get("visual_settings")
    try:
        validate_editor_payload(content, order, settings)
        return store.update_version(job_id, version_id, content, order, settings)
    except ValueError as exc:
        fail(422, "validation_error", str(exc))
    except KeyError:
        fail(404, "not_found", "岗位简历版本不存在。")


@app.delete("/api/jobs/{job_id}/versions/{version_id}")
def delete_version(job_id: str, version_id: str, store: CloudStore = Depends(cloud_store)):
    try:
        store.delete_version(job_id, version_id)
        return {"ok": True}
    except KeyError:
        fail(404, "not_found", "岗位简历版本不存在。")


@app.get("/api/interviews")
def list_interviews(job_id: str | None = None, store: CloudStore = Depends(cloud_store)):
    return store.list_interviews(job_id)


@app.get("/api/interviews/{session_id}")
def get_interview(session_id: str, store: CloudStore = Depends(cloud_store)):
    session = store.get_interview(session_id)
    if session is None:
        fail(404, "not_found", "面试记录不存在。")
    return session


@app.post("/api/jobs/{job_id}/versions/{version_id}/interviews")
def start_interview(job_id: str, version_id: str, store: CloudStore = Depends(cloud_store)):
    if not store.load_resume():
        fail(409, "resume_required", "请先保存基础简历。")
    try:
        session = store.create_interview(job_id, version_id)
    except KeyError:
        fail(404, "not_found", "岗位简历版本不存在。")
    queued = _task(store, "interview_question", {"session_id": session["id"]})
    return {"session_id": session["id"], **queued}


@app.post("/api/interviews/{session_id}/answers")
def answer_interview(session_id: str, payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    session = store.get_interview(session_id)
    if session is None:
        fail(404, "not_found", "面试记录不存在。")
    turns = session.get("turns") or []
    if session.get("status") not in {"awaiting_answer", "failed"} or len(turns) != 12:
        fail(409, "invalid_state", "当前没有待评价的问题。")
    turn_id = payload.get("turn_id")
    turn_ids = payload.get("turn_ids")
    if turn_ids is not None:
        if not isinstance(turn_ids, list) or len(turn_ids) != 12 or not all(isinstance(value, str) for value in turn_ids) or set(turn_ids) != {turn["id"] for turn in turns}:
            fail(422, "validation_error", "请填写全部 12 题后统一提交。")
        for turn in turns:
            if not turn.get("feedback") and not (turn.get("answer") or "").strip():
                fail(422, "validation_error", "请填写全部 12 题后统一提交。")
        selected = [turn for turn in turns if not turn.get("feedback")]
    else:
        selected = [turn for turn in turns if turn["id"] == turn_id]
        if len(selected) != 1 or selected[0].get("feedback"):
            fail(409, "invalid_state", "此题已评价或不存在。")
        answer = payload.get("answer")
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 4000:
            fail(422, "validation_error", "请填写不超过 4000 字的回答。")
        selected[0]["answer"] = answer.strip()
    if not selected:
        return session
    now = datetime.now(timezone.utc).isoformat()
    selected_ids = {turn["id"] for turn in selected}
    turns = [{**turn, "answer": (turn.get("answer") or "").strip(),
              "answered_at": turn.get("answered_at") or now} if turn["id"] in selected_ids else turn for turn in turns]
    try:
        store.update_interview(session_id, {"turns": turns, "status": "feedback_pending"}, expected_status=session["status"])
    except ValueError:
        fail(409, "conflict", "面试状态已变化，请刷新后重试。")
    return _task(store, "interview_feedback", {"session_id": session_id, "turn_ids": [turn["id"] for turn in selected]})


@app.patch("/api/interviews/{session_id}/drafts")
def save_interview_draft(session_id: str, payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    answer = payload.get("answer")
    if not isinstance(answer, str) or len(answer) > 4000:
        fail(422, "validation_error", "草稿不能超过 4000 字。")
    session = store.get_interview(session_id)
    if session is None:
        fail(404, "not_found", "面试记录不存在。")
    turns = session.get("turns") or []
    if session.get("status") not in {"awaiting_answer", "failed"}:
        fail(409, "invalid_state", "当前不能保存草稿。")
    selected = [turn for turn in turns if turn["id"] == payload.get("turn_id")]
    if len(selected) != 1 or selected[0].get("answered_at") or selected[0].get("feedback"):
        fail(409, "invalid_state", "此题不能保存草稿。")
    selected[0]["answer"] = answer
    try:
        return store.update_interview(session_id, {"turns": turns}, expected_status=session["status"])
    except ValueError:
        fail(409, "conflict", "面试状态已变化，请刷新后重试。")


@app.post("/api/interviews/{session_id}/reference")
def reference_interview(session_id: str, store: CloudStore = Depends(cloud_store)):
    from .interview_ai import upgrade_saved_reference
    session = store.get_interview(session_id)
    if session is None:
        fail(404, "not_found", "面试记录不存在。")
    turns = session.get("turns") or []
    if session.get("status") != "awaiting_answer" or not turns or turns[-1].get("answer"):
        fail(409, "invalid_state", "当前没有可查看参考答案的题目。")
    if turns[-1].get("reference_answer"):
        resume = store.load_resume()
        upgraded = upgrade_saved_reference(session, resume or {}) if resume else None
        if upgraded:
            turns[-1] = {**turns[-1], "reference_answer": upgraded}
            store.update_interview(session_id, {"turns": turns}, expected_status="awaiting_answer")
        return {"session_id": session_id, "status": "ready"}
    try:
        store.update_interview(session_id, {"status": "reference_pending"}, expected_status="awaiting_answer")
    except ValueError:
        fail(409, "conflict", "面试状态已变化，请刷新后重试。")
    try:
        return _task(store, "interview_reference", {"session_id": session_id, "turn_id": turns[-1]["id"]})
    except HTTPException:
        try:
            store.update_interview(session_id, {"status": "awaiting_answer"}, expected_status="reference_pending")
        except (KeyError, ValueError):
            pass
        raise


@app.post("/api/interviews/{session_id}/next")
def next_interview(session_id: str, store: CloudStore = Depends(cloud_store)):
    session = store.get_interview(session_id)
    if session is None:
        fail(404, "not_found", "面试记录不存在。")
    turns = session.get("turns") or []
    status = session.get("status")
    if status != "failed" or turns:
        fail(409, "invalid_state", "本场题目已生成。")
    store.update_interview(session_id, {"status": "question_pending"}, expected_status=status)
    return _task(store, "interview_question", {"session_id": session_id})


@app.post("/api/interviews/{session_id}/end")
def end_interview(session_id: str, store: CloudStore = Depends(cloud_store)):
    session = store.get_interview(session_id)
    if session is None:
        fail(404, "not_found", "面试记录不存在。")
    if session.get("status") == "ended":
        return session
    try:
        return store.update_interview(session_id, {"status": "ended"}, expected_status=session["status"])
    except ValueError:
        fail(409, "conflict", "面试状态已变化，请刷新后重试。")


@app.post("/api/uploads")
def create_upload(payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    filename, size = payload.get("filename"), payload.get("size")
    suffix = str(filename or "").rsplit(".", 1)[-1].lower()
    if suffix not in SUFFIXES or "." not in str(filename):
        fail(415, "unsupported_format", "仅支持 .docx、.doc 和 PDF。")
    if not isinstance(size, int) or size <= 0 or size > MAX_UPLOAD:
        fail(413, "file_too_large", "文件大小不能超过 15 MB。")
    upload_id = uuid.uuid4().hex
    key = f"uploads/{store.user_id}/{upload_id}.{suffix}"
    url = aws_client("s3").generate_presigned_url("put_object", Params={"Bucket": os.environ["CV_FILES_BUCKET"], "Key": key}, ExpiresIn=600)
    return {"upload_id": upload_id, "key": key, "url": url}


@app.post("/api/import")
def import_resume(payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    key = payload.get("key")
    if not isinstance(key, str) or not re.fullmatch(rf"uploads/{re.escape(store.user_id)}/[a-f0-9]{{32}}\.(doc|docx|pdf)", key):
        fail(422, "validation_error", "上传文件无效。")
    return _task(store, "import", {"key": key})


@app.post("/api/jobs/extract")
def extract_job(payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    key = payload.get("key")
    if not isinstance(key, str) or not re.fullmatch(rf"uploads/{re.escape(store.user_id)}/[a-f0-9]{{32}}\.(doc|docx|pdf)", key):
        fail(422, "validation_error", "上传文件无效。")
    return _task(store, "extract_job", {"key": key})


@app.post("/api/import/{draft_id}/confirm")
def confirm_import(draft_id: str, resume: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    task = store.get_task(draft_id)
    if not task or task.get("kind") != "import" or task.get("status") != "succeeded":
        fail(404, "draft_expired", "导入草稿已失效，请重新上传。")
    validate(resume, "base-resume.schema.json")
    validate_required_base_fields(resume)
    store.save_resume(resume)
    return resume


@app.post("/api/generate")
def generate(payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    if not store.load_resume():
        fail(409, "resume_required", "请先保存基础简历。")
    job_text = payload.get("job_text")
    if not isinstance(job_text, str) or not job_text.strip():
        fail(422, "validation_error", "请粘贴招聘要求文本。")
    return _task(store, "generate", {"job_text": job_text})


@app.post("/api/export")
def export(payload: dict[str, Any], store: CloudStore = Depends(cloud_store)):
    job_id, version_id = payload.get("job_id"), payload.get("version_id")
    template_id, fmt = payload.get("template_id"), payload.get("format")
    if template_id not in TEMPLATE_IDS or fmt not in {"docx", "pdf"}:
        fail(422, "validation_error", "请选择有效模板和导出格式。")
    try:
        _, job = store.get_job(job_id)
        if not any(version.get("id") == version_id for version in job["versions"]):
            raise KeyError(version_id)
    except (KeyError, TypeError):
        fail(404, "not_found", "岗位简历版本不存在。")
    return _task(store, "export", {"job_id": job_id, "version_id": version_id, "template_id": template_id, "format": fmt})


@app.get("/api/tasks/{task_id}")
def task_status(task_id: str, store: CloudStore = Depends(cloud_store)):
    task = store.get_task(task_id)
    if not task:
        fail(404, "not_found", "任务不存在。")
    visible = {key: task.get(key) for key in ("id", "kind", "status", "result", "error", "created_at", "updated_at")}
    result = visible.get("result") or {}
    key = result.get("result_key") if isinstance(result, dict) else None
    if key:
        if key != f"results/{store.user_id}/{task_id}.json":
            fail(500, "invalid_result", "任务结果无效。")
        obj = aws_client("s3").get_object(Bucket=os.environ["CV_FILES_BUCKET"], Key=key)
        with obj["Body"] as body:
            visible["result"] = __import__("json").loads(body.read())
    return visible


@app.get("/api/download/{task_id}")
def download(task_id: str, store: CloudStore = Depends(cloud_store)):
    task = store.get_task(task_id)
    if not task or task.get("kind") != "export" or task.get("status") != "succeeded":
        fail(404, "not_found", "导出文件不存在。")
    key = (task.get("result") or {}).get("key")
    if not isinstance(key, str) or not key.startswith(f"exports/{store.user_id}/{task_id}."):
        fail(404, "not_found", "导出文件不存在。")
    url = aws_client("s3").generate_presigned_url("get_object", Params={"Bucket": os.environ["CV_FILES_BUCKET"], "Key": key}, ExpiresIn=300)
    return {"url": url, "format": (task.get("result") or {}).get("format")}


handler = Mangum(app, lifespan="off") if Mangum else None
