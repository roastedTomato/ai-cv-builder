"""Local-only FastAPI app. Run with ``cvbuilder-web`` or ``python -m cvbuilder.web``.

API contract:
* GET /api/resume -> resume | null; PUT /api/resume -> validated resume
* DELETE /api/resume, POST /api/reset -> {ok: true}
* GET /api/templates -> [{id,name}]
* GET /api/applications -> nested companies, jobs, and saved versions
* POST /api/companies; POST /api/companies/{id}/jobs
* PATCH/DELETE /api/companies/{id}; PATCH/DELETE /api/jobs/{id}
* POST /api/jobs/{id}/versions generates V1 once
* POST /api/jobs/{id}/versions/{version_id}/edits saves the next version
* DELETE /api/jobs/{id}/versions/{version_id} removes one version
* POST /api/import -> {draft_id,format,draft}; POST /api/import/{id}/confirm -> resume
* POST /api/generate {job_text} -> unverified editable resume draft
* POST /api/export {template_id,format,body_text} -> DOCX/PDF download
Errors always use {"error":{"code":"...","message":"..."}}.
Import drafts live only in memory until explicit confirmation. Edited resume
payloads are export-only and are not treated as AI-validated facts.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jsonschema import Draft202012Validator
from starlette.requests import Request

from . import ai
from .cli import ROOT, read_json
from .importers import ImportErrorBase, extract_text
from .importers.fields import extract_personal, keep_explicit_skills
from .mysql_storage import MySQLResumeStore, MySQLJobStore
from .templates import DEFAULT_TEMPLATE_ID, PUBLIC_TEMPLATES, TEMPLATE_IDS, convert_to_pdf, fill_template, fill_template_text, fill_template_modular

SCHEMAS = ROOT / "docs" / "schemas"
WEB_DIR = ROOT / "web"
app = FastAPI(title="本地简历生成平台")
logger = logging.getLogger("cvbuilder.web")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
pages = Jinja2Templates(directory=WEB_DIR / "templates")
store = MySQLResumeStore()
job_store = MySQLJobStore()
drafts: dict[str, dict[str, Any]] = {}
RESUME_MODULES = {"summary", "education", "work", "projects", "skills", "certificates"}


def _interview_error(exc: Exception, fallback: str) -> dict[str, str]:
    if getattr(exc, "status_code", None) == 401:
        return {"code": "model_auth_error", "message": "OpenAI API key 无效或已失效，请更新配置后重试。"}
    return {"code": "ai_error", "message": fallback}


def validate_editor_payload(content: Any, order: Any, settings: Any) -> None:
    """Validate the version envelope without schema/fact-checking generated content."""
    if not isinstance(content, dict) or not isinstance(order, list) or not all(isinstance(item, str) for item in order) or len(order) != len(set(order)) or not set(order).issubset(RESUME_MODULES) or not isinstance(settings, dict):
        raise ValueError("简历内容、模块顺序或外观设置无效。")
    if settings.get("template_id", "template-01") not in TEMPLATE_IDS:
        raise ValueError("请选择有效模板。")
    if settings.get("page_width", "standard") not in {"narrow", "standard", "wide"}:
        raise ValueError("页面宽度设置无效。")
    color = settings.get("color", "#2C5263")
    if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError("主题颜色必须是 HEX 色值。")


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Reject requests whose Host header targets a non-loopback interface."""
    host = request.headers.get("host", "").split(":", 1)[0].strip("[]").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"error": {"code": "local_only", "message": "此服务只接受本机访问。"}})
    return await call_next(request)


def validate(data: Any, schema_name: str) -> None:
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in errors[:5])
        raise HTTPException(422, detail={"code": "validation_error", "message": details})


def validate_required_base_fields(resume: dict[str, Any]) -> None:
    """Require the user-confirmed identifying fields before local persistence."""
    fields = []
    for key, label in (("name", "姓名"), ("email", "邮箱"), ("phone", "电话")):
        if not resume["personal"][key].strip():
            fields.append(label)
    required_sections = {
        "education": (("school", "学校"), ("degree", "学历"), ("major", "专业")),
        "work": (("employer", "公司"), ("title", "职位")),
        "projects": (("name", "项目名"), ("role", "角色")),
        "skills": (("name", "技能名称"),),
        "certificates": (("name", "证书名称"),),
    }
    for section, required in required_sections.items():
        for index, item in enumerate(resume[section], 1):
            for key, label in required:
                if not item[key].strip():
                    fields.append(f"第 {index} 条{label}")
    if fields:
        raise HTTPException(422, detail={"code": "required_fields", "message": "请填写必填字段：" + "、".join(fields)})


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    from fastapi.responses import JSONResponse
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "request_error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(Exception)
async def safe_error_handler(request: Request, exc: Exception):
    from fastapi.responses import JSONResponse
    # Never return provider exception text: it can contain sensitive request data.
    logger.error("Unhandled request error path=%s type=%s", request.url.path, type(exc).__name__)
    from pymysql.err import InterfaceError, OperationalError
    if (isinstance(exc, RuntimeError) and str(exc).startswith("MySQL ")) or isinstance(exc, (InterfaceError, OperationalError)):
        return JSONResponse(status_code=503, content={"error": {"code": "database_unavailable", "message": "无法连接本地 MySQL，请检查数据库服务及 CV_MYSQL_* 环境变量。"}})
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "操作失败，请检查配置后重试。"}})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return pages.TemplateResponse(request, "index.html", {})


@app.get("/api/templates")
async def list_templates():
    return list(PUBLIC_TEMPLATES)


@app.get("/api/resume")
async def get_resume():
    return store.load()


@app.put("/api/resume")
async def put_resume(resume: dict[str, Any]):
    validate(resume, "base-resume.schema.json")
    validate_required_base_fields(resume)
    store.save(resume)
    return resume


@app.delete("/api/resume")
async def delete_resume():
    store.delete()
    return {"ok": True}


@app.post("/api/reset")
async def reset_data():
    store.reset()
    job_store.reset()
    drafts.clear()
    return {"ok": True}


@app.get("/api/applications")
async def get_applications():
    return job_store.list()


@app.post("/api/companies")
async def create_company(payload: dict[str, Any]):
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(422, detail={"code": "validation_error", "message": "请填写公司名称。"})
    try:
        return job_store.add_company(name.strip())
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "validation_error", "message": str(exc)}) from exc


@app.patch("/api/companies/{company_id}")
async def rename_company(company_id: str, payload: dict[str, Any]):
    try:
        return job_store.rename_company(company_id, payload.get("name"))
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "公司记录不存在。"}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "validation_error", "message": str(exc)}) from exc


@app.delete("/api/companies/{company_id}")
async def delete_company(company_id: str):
    try:
        job_store.delete_company(company_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "公司记录不存在。"}) from exc


@app.post("/api/companies/{company_id}/jobs")
async def create_job(company_id: str, payload: dict[str, Any]):
    title, job_text = payload.get("title"), payload.get("job_text")
    if not isinstance(title, str) or not title.strip() or not isinstance(job_text, str) or not job_text.strip():
        raise HTTPException(422, detail={"code": "validation_error", "message": "请填写职位名称和招聘要求。"})
    try:
        return job_store.add_job(company_id, title.strip(), job_text.strip())
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "公司记录不存在。"}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "validation_error", "message": str(exc)}) from exc


@app.patch("/api/jobs/{job_id}")
async def rename_job(job_id: str, payload: dict[str, Any]):
    try:
        if "job_text" in payload:
            return job_store.update_job(job_id, payload.get("title"), payload.get("job_text"), payload.get("expected_revision"))
        return job_store.rename_job(job_id, payload.get("title"))
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位记录不存在。"}) from exc
    except ValueError as exc:
        conflict = "请刷新后重试" in str(exc) or "并发" in str(exc)
        raise HTTPException(409 if conflict else 422, detail={"code": "conflict" if conflict else "validation_error", "message": str(exc)}) from exc


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    try:
        job_store.delete_job(job_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位记录不存在。"}) from exc


@app.post("/api/jobs/{job_id}/versions")
async def create_job_version(job_id: str):
    state = job_store.load()
    parent = next(((company, job) for company in state["companies"] for job in company.get("jobs", []) if job.get("id") == job_id), None)
    job = parent[1] if parent else None
    if job is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位记录不存在。"})
    if job.get("has_generated") or job.get("versions"):
        raise HTTPException(409, detail={"code": "version_exists", "message": "该岗位已生成 V1；请编辑现有版本创建新版。"})
    resume = store.load()
    if not resume:
        raise HTTPException(409, detail={"code": "resume_required", "message": "请先保存基础简历。"})
    try:
        content = ai.generate_resume_from_job_unchecked(resume, job["job_text"])
    except Exception as exc:
        logger.error("Resume generation failed type=%s", type(exc).__name__)
        message = str(exc) if isinstance(exc, (ai.ModelConfigurationError, ai.ModelResponseError)) else "简历生成失败，请检查 API 配置后重试。"
        raise HTTPException(502, detail={"code": "ai_error", "message": message}) from exc
    module_order = [key for key in ("summary", "education", "work", "projects", "skills", "certificates") if content.get(key)]
    try:
        return job_store.create_version(parent[0]["id"], job_id, content, module_order, {"template_id": DEFAULT_TEMPLATE_ID, "color": "#2C5263", "page_width": "standard"}, job_text=job["job_text"])
    except ValueError as exc:
        code = "conflict" if "请刷新后重试" in str(exc) else "version_exists"
        raise HTTPException(409, detail={"code": code, "message": str(exc)}) from exc


@app.post("/api/jobs/{job_id}/versions/{version_id}/edits")
async def update_job_version(job_id: str, version_id: str, payload: dict[str, Any]):
    try:
        content = payload.get("resume_content")
        order = payload.get("module_order")
        settings = payload.get("visual_settings")
        validate_editor_payload(content, order, settings)
        return job_store.save_edited_version(job_id, version_id, content, order, settings)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位简历版本不存在。"}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "validation_error", "message": str(exc)}) from exc


@app.put("/api/jobs/{job_id}/versions/{version_id}/draft")
async def save_resume_draft(job_id: str, version_id: str, payload: dict[str, Any]):
    content, order, settings = payload.get("resume_content"), payload.get("module_order"), payload.get("visual_settings")
    try:
        validate_editor_payload(content, order, settings)
        return job_store.save_resume_draft(job_id, version_id, content, order, settings)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位简历版本不存在。"}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "validation_error", "message": str(exc)}) from exc


@app.put("/api/jobs/{job_id}/versions/{version_id}")
async def overwrite_resume_version(job_id: str, version_id: str, payload: dict[str, Any]):
    content, order, settings = payload.get("resume_content"), payload.get("module_order"), payload.get("visual_settings")
    try:
        validate_editor_payload(content, order, settings)
        return job_store.update_version(job_id, version_id, content, order, settings)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位简历版本不存在。"}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "validation_error", "message": str(exc)}) from exc


@app.delete("/api/jobs/{job_id}/versions/{version_id}")
async def delete_version(job_id: str, version_id: str):
    try:
        job_store.delete_version(job_id, version_id)
        return {"ok": True}
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位简历版本不存在。"}) from exc


@app.get("/api/interviews")
async def list_interviews(job_id: str | None = None):
    return job_store.list_interviews(job_id)


@app.get("/api/interviews/{session_id}")
async def get_interview(session_id: str):
    session = job_store.get_interview(session_id)
    if session is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "面试记录不存在。"})
    return session


def _local_question(session_id: str, expected_status: str) -> dict[str, Any]:
    from datetime import datetime, timezone
    from .interview_ai import generate_questions
    session = job_store.get_interview(session_id)
    resume = store.load()
    if not resume:
        raise HTTPException(409, detail={"code": "resume_required", "message": "请先保存基础简历。"})
    try:
        generated = generate_questions(session, resume)
        turns = [{"id": uuid.uuid4().hex, "question": item["question"],
                  "category": item["category"], "reference_answer": item["reference_answer"],
                  "answer": None, "feedback": None, "follow_up": None,
                  "created_at": datetime.now(timezone.utc).isoformat()} for item in generated]
        return job_store.update_interview(session_id, {"turns": turns,
                                                      "status": "awaiting_answer", "error": None}, expected_status=expected_status)
    except Exception as exc:
        logger.error("Interview question failed type=%s", type(exc).__name__)
        error = _interview_error(exc, "问题与参考答案生成失败，请重试。")
        job_store.update_interview(session_id, {"status": "failed", "error": error}, expected_status=expected_status)
        raise HTTPException(502, detail=error) from exc


@app.post("/api/jobs/{job_id}/versions/{version_id}/interviews")
async def start_interview(job_id: str, version_id: str):
    if not store.load():
        raise HTTPException(409, detail={"code": "resume_required", "message": "请先保存基础简历。"})
    try:
        session = job_store.create_interview(job_id, version_id)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位简历版本不存在。"}) from exc
    return _local_question(session["id"], "new")


@app.post("/api/interviews/{session_id}/reference")
async def reference_interview(session_id: str):
    from .interview_ai import generate_reference_answer, upgrade_saved_reference
    session = job_store.get_interview(session_id)
    if session is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "面试记录不存在。"})
    turns = session.get("turns") or []
    if session.get("status") != "awaiting_answer" or not turns or turns[-1].get("answer"):
        raise HTTPException(409, detail={"code": "invalid_state", "message": "当前没有可查看参考答案的题目。"})
    if turns[-1].get("reference_answer"):
        resume = store.load()
        upgraded = upgrade_saved_reference(session, resume or {}) if resume else None
        if upgraded:
            turns[-1] = {**turns[-1], "reference_answer": upgraded}
            return job_store.update_interview(session_id, {"turns": turns}, expected_status="awaiting_answer")
        return session
    resume = store.load()
    if not resume:
        raise HTTPException(409, detail={"code": "resume_required", "message": "请先保存基础简历。"})
    try:
        pending = job_store.update_interview(session_id, {"status": "reference_pending"}, expected_status="awaiting_answer")
        reference = generate_reference_answer(pending, resume)
        turns[-1] = {**turns[-1], "reference_answer": reference}
        return job_store.update_interview(session_id, {"turns": turns, "status": "awaiting_answer", "error": None}, expected_status="reference_pending")
    except Exception as exc:
        logger.error("Interview reference failed type=%s", type(exc).__name__)
        try:
            job_store.update_interview(session_id, {"status": "awaiting_answer"}, expected_status="reference_pending")
        except (KeyError, ValueError):
            pass
        raise HTTPException(502, detail={"code": "ai_error", "message": "旧面试题的参考答案生成失败，请重试或继续回答。新题目会同时生成问题和参考答案。"}) from exc


@app.post("/api/interviews/{session_id}/answers")
async def answer_interview(session_id: str, payload: dict[str, Any]):
    from datetime import datetime, timezone
    from .interview_ai import generate_feedback
    session = job_store.get_interview(session_id)
    if session is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "面试记录不存在。"})
    turns = session.get("turns") or []
    if session.get("status") not in {"awaiting_answer", "failed"} or len(turns) != 12:
        raise HTTPException(409, detail={"code": "invalid_state", "message": "当前不能提交回答。"})
    turn_id = payload.get("turn_id")
    turn_ids = payload.get("turn_ids")
    if isinstance(turn_ids, list):
        if len(turn_ids) != 12 or set(turn_ids) != {turn["id"] for turn in turns}:
            raise HTTPException(422, detail={"code": "validation_error", "message": "请先填写全部 12 题。"})
        pending_ids = [turn["id"] for turn in turns if not turn.get("feedback")]
    elif isinstance(turn_id, str):
        pending_ids = [turn_id]
    else:
        raise HTTPException(422, detail={"code": "validation_error", "message": "请选择要提交的题目。"})
    if not pending_ids:
        return session
    answer = payload.get("answer")
    for index, turn in enumerate(turns):
        if turn["id"] not in pending_ids:
            continue
        if turn.get("feedback"):
            raise HTTPException(409, detail={"code": "invalid_state", "message": "该题已评价。"})
        text = answer if turn["id"] == turn_id else turn.get("answer")
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise HTTPException(422, detail={"code": "validation_error", "message": "请填写不超过 4000 字的回答。"})
        turns[index] = {**turn, "answer": text.strip(),
                        "answered_at": turn.get("answered_at") or datetime.now(timezone.utc).isoformat()}
    try:
        session = job_store.update_interview(session_id, {"turns": turns, "status": "feedback_pending"}, expected_status=session["status"])
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "conflict", "message": "面试状态已变化，请刷新后重试。"}) from exc
    resume = store.load()
    if not resume:
        job_store.update_interview(session_id, {"status": "failed"}, expected_status="feedback_pending")
        raise HTTPException(409, detail={"code": "resume_required", "message": "基础简历已删除，回答已保存。"})
    try:
        for turn_id in pending_ids:
            current = next(turn for turn in session["turns"] if turn["id"] == turn_id)
            feedback = generate_feedback({**session, "turns": [current]}, resume)
            updated_turns = [{**turn, "feedback": feedback, "follow_up": None} if turn["id"] == turn_id else turn
                             for turn in session["turns"]]
            session = job_store.update_interview(session_id, {"turns": updated_turns}, expected_status="feedback_pending")
        return job_store.update_interview(session_id, {"status": "awaiting_answer", "error": None}, expected_status="feedback_pending")
    except Exception as exc:
        logger.error("Interview feedback failed type=%s", type(exc).__name__)
        error = _interview_error(exc, "回答反馈生成失败，回答已保存，请重试。")
        job_store.update_interview(session_id, {"status": "failed", "error": error}, expected_status="feedback_pending")
        raise HTTPException(502, detail=error) from exc


@app.post("/api/interviews/{session_id}/next")
async def next_interview(session_id: str):
    session = job_store.get_interview(session_id)
    if session is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "面试记录不存在。"})
    if session["status"] == "failed" and not session["turns"]:
        job_store.update_interview(session_id, {"status": "question_pending"}, expected_status="failed")
        return _local_question(session_id, "question_pending")
    raise HTTPException(409, detail={"code": "invalid_state", "message": "题目已全部生成。"})


@app.patch("/api/interviews/{session_id}/drafts")
async def save_interview_draft(session_id: str, payload: dict[str, Any]):
    session = job_store.get_interview(session_id)
    if session is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "面试记录不存在。"})
    if session["status"] not in {"awaiting_answer", "failed"}:
        raise HTTPException(409, detail={"code": "invalid_state", "message": "当前不能保存草稿。"})
    answer, turn_id = payload.get("answer"), payload.get("turn_id")
    if not isinstance(answer, str) or len(answer) > 4000:
        raise HTTPException(422, detail={"code": "validation_error", "message": "回答不能超过 4000 字。"})
    if not any(turn["id"] == turn_id and not turn.get("answered_at") for turn in session["turns"]):
        raise HTTPException(409, detail={"code": "invalid_state", "message": "该题不能编辑。"})
    turns = [{**turn, "answer": answer} if turn["id"] == turn_id else turn for turn in session["turns"]]
    try:
        return job_store.update_interview(session_id, {"turns": turns}, expected_status=session["status"])
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "conflict", "message": "面试状态已变化，请刷新后重试。"}) from exc


@app.post("/api/interviews/{session_id}/end")
async def end_interview(session_id: str):
    session = job_store.get_interview(session_id)
    if session is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "面试记录不存在。"})
    if session.get("status") == "ended":
        return session
    return job_store.update_interview(session_id, {"status": "ended"}, expected_status=session["status"])


@app.post("/api/import")
async def import_resume(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".docx", ".doc", ".pdf"}:
        raise HTTPException(415, detail={"code": "unsupported_format", "message": "仅支持 .docx、.doc 和 PDF。"})
    try:
        with tempfile.TemporaryDirectory(prefix="cvbuilder-upload-") as folder:
            path = Path(folder) / ("upload" + suffix)
            content = await file.read(15 * 1024 * 1024 + 1)
            if len(content) > 15 * 1024 * 1024:
                raise HTTPException(413, detail={"code": "file_too_large", "message": "文件大小不能超过 15 MB。"})
            path.write_bytes(content)
            extracted = extract_text(path)
        personal = extract_personal(extracted.text)
        draft = ai.structure_resume(extracted.text, personal)
        model_personal = draft.get("personal", {})
        draft["personal"] = {
            key: personal[key] or (model_personal.get(key, "") if isinstance(model_personal, dict) else "")
            for key in ("name", "email", "phone", "location")
        }
        keep_explicit_skills(draft, extracted.text)
        validate(draft, "base-resume.schema.json")
        draft_id = uuid.uuid4().hex
        drafts[draft_id] = draft
        return {"draft_id": draft_id, "format": extracted.format, "draft": draft}
    except ImportErrorBase as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    except HTTPException:
        raise
    except Exception as exc:
        error_meta = {
            key: getattr(exc, key, None)
            for key in ("status_code", "code", "param", "type", "request_id")
            if getattr(exc, key, None) is not None
        }
        if isinstance(exc, ai.ModelSchemaValidationError):
            error_meta["fields"] = exc.fields
            error_meta["validators"] = exc.validators
            error_meta["missing"] = exc.missing
        # These SDK fields identify the failing API parameter without logging
        # exception text, request/response bodies, uploaded text, or credentials.
        error_code = getattr(exc, "code", None)
        logger.error("Resume import failed stage=structure type=%s code=%s metadata=%s", type(exc).__name__, error_code or "", json.dumps(error_meta, ensure_ascii=True))
        message = "简历内容已提取，但 AI 结构化失败。请确认服务器已重启、OPENAI_API_KEY 有效且账户可调用当前模型；也可以手动填写。"
        if isinstance(exc, ai.ModelConfigurationError):
            message = str(exc)
        elif isinstance(exc, ai.ModelSchemaValidationError):
            message = "模型已返回结果，但未通过基础简历 Schema 校验。请检查导入文档内容或手动填写。"
        elif isinstance(exc, ai.ModelResponseError):
            message = str(exc)
        elif isinstance(exc, RuntimeError) and str(exc) in {"模型未返回结构化 JSON 内容", "模型返回内容不是有效 JSON"}:
            message = str(exc)
        elif type(exc).__name__ == "BadRequestError":
            param = getattr(exc, "param", None)
            code = getattr(exc, "code", None)
            if param == "model" or code in {"model_not_found", "unsupported_model"}:
                message = "OpenAI 不接受当前 OPENAI_MODEL。请在启动服务的终端设置为账户可用且支持 Structured Outputs 的模型名称后重启。"
            elif param and ("text" in str(param) or "schema" in str(param)):
                message = "OpenAI 拒绝了结构化输出参数。请检查当前模型对 Structured Outputs 的支持；服务器日志会显示被拒绝的参数名。"
            else:
                message = "OpenAI 拒绝了请求（HTTP 400）。请检查当前 OPENAI_MODEL 是否可用，并确认账户支持 Responses API 与 Structured Outputs。服务器日志只会记录安全错误元数据。"
        elif type(exc).__name__ == "AuthenticationError":
            message = "OpenAI API 凭据无效或已失效，请检查 OPENAI_API_KEY。"
        elif type(exc).__name__ == "PermissionDeniedError":
            message = "当前 API 项目无权调用所选模型，请检查模型权限或设置 OPENAI_MODEL。"
        elif type(exc).__name__ == "NotFoundError":
            message = "找不到所选模型，请检查 OPENAI_MODEL 的模型名称。"
        elif type(exc).__name__ == "RateLimitError":
            message = "OpenAI API 请求受限，请检查账户额度或稍后重试。"
        raise HTTPException(502, detail={"code": "ai_error", "message": message}) from exc


@app.post("/api/jobs/extract")
async def extract_job(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".docx", ".doc", ".pdf"}:
        raise HTTPException(415, detail={"code": "unsupported_format", "message": "仅支持 .docx、.doc 和 PDF。"})
    with tempfile.TemporaryDirectory(prefix="cvbuilder-job-upload-") as folder:
        path = Path(folder) / ("upload" + suffix)
        content = await file.read(15 * 1024 * 1024 + 1)
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(413, detail={"code": "file_too_large", "message": "文件大小不能超过 15 MB。"})
        path.write_bytes(content)
        try:
            extracted = extract_text(path)
        except ImportErrorBase as exc:
            raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    text = extracted.text.strip()
    if not text:
        raise HTTPException(422, detail={"code": "no_text", "message": "文件没有可提取的文字；扫描版 PDF 暂不支持。"})
    return {"text": text, "format": extracted.format}


@app.post("/api/import/{draft_id}/confirm")
async def confirm_import(draft_id: str, resume: dict[str, Any]):
    if draft_id not in drafts:
        raise HTTPException(404, detail={"code": "draft_expired", "message": "导入草稿已失效，请重新上传。"})
    validate(resume, "base-resume.schema.json")
    validate_required_base_fields(resume)
    store.save(resume)
    drafts.pop(draft_id, None)
    return resume


@app.post("/api/generate")
async def generate(payload: dict[str, Any]):
    resume = store.load()
    if not resume:
        raise HTTPException(409, detail={"code": "resume_required", "message": "请先保存基础简历。"})
    job_text = payload.get("job_text")
    if not isinstance(job_text, str) or not job_text.strip():
        raise HTTPException(422, detail={"code": "validation_error", "message": "请粘贴招聘要求文本。"})
    try:
        return ai.generate_resume_from_job_unchecked(resume, job_text)
    except Exception as exc:
        logger.error("Resume generation failed type=%s", type(exc).__name__)
        message = str(exc) if isinstance(exc, (ai.ModelConfigurationError, ai.ModelResponseError)) else "简历生成失败，请检查 API 配置后重试。"
        raise HTTPException(502, detail={"code": "ai_error", "message": message}) from exc


@app.post("/api/export")
async def export_resume(payload: dict[str, Any]):
    template_id, fmt, resume = payload.get("template_id"), payload.get("format"), payload.get("resume")
    body_text = payload.get("body_text")
    resume_content = payload.get("resume_content")
    module_order = payload.get("module_order")
    visual_settings = payload.get("visual_settings") or {}
    templates = {template: ROOT / "templates" / f"{template}.docx" for template in TEMPLATE_IDS}
    if template_id not in templates or fmt not in {"docx", "pdf"}:
        raise HTTPException(422, detail={"code": "validation_error", "message": "请选择有效模板和导出格式。"})
    if resume_content is not None:
        try:
            validate_editor_payload(resume_content, module_order, visual_settings)
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "validation_error", "message": str(exc)}) from exc
    elif body_text is None:
        validate(resume, "generated-resume.schema.json")
    elif not isinstance(body_text, str) or len(body_text.strip()) < 3:
        raise HTTPException(422, detail={"code": "validation_error", "message": "请先生成并检查简历正文。"})
    try:
        with tempfile.TemporaryDirectory(prefix="cvbuilder-export-") as folder:
            path = Path(folder) / "resume.docx"
            if resume_content is not None:
                fill_template_modular(templates[template_id], resume_content, module_order, visual_settings, path)
            elif body_text is None:
                fill_template(templates[template_id], resume, path)
            else:
                fill_template_text(templates[template_id], body_text, path)
            if fmt == "pdf":
                path = convert_to_pdf(path)
            data = path.read_bytes()
        from fastapi.responses import Response
        media = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="resume.{fmt}"'})
    except Exception as exc:
        raise HTTPException(422, detail={"code": "export_error", "message": "导出失败，请检查模板或 LibreOffice 配置。"}) from exc


def main() -> None:
    import uvicorn
    uvicorn.run("cvbuilder.web:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
