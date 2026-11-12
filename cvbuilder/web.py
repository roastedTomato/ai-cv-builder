"""Local-only FastAPI app. Run with ``cvbuilder-web`` or ``python -m cvbuilder.web``.

API contract:
* GET /api/resume -> resume | null; PUT /api/resume -> validated resume
* DELETE /api/resume, POST /api/reset -> {ok: true}
* GET /api/templates -> [{id,name}]
* GET /api/applications -> nested companies, jobs, and saved versions
* POST /api/companies; POST /api/companies/{id}/jobs
* POST /api/jobs/{id}/versions generates and persists a modular draft
* PUT /api/jobs/{id}/versions/{version_id} updates the selected version
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
from .storage import ResumeStore
from .job_storage import JobRecordStore
from .templates import convert_to_pdf, fill_template, fill_template_text, fill_template_modular

SCHEMAS = ROOT / "docs" / "schemas"
WEB_DIR = ROOT / "web"
app = FastAPI(title="本地简历生成平台")
logger = logging.getLogger("cvbuilder.web")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
pages = Jinja2Templates(directory=WEB_DIR / "templates")
store = ResumeStore()
job_store = JobRecordStore()
drafts: dict[str, dict[str, Any]] = {}
RESUME_MODULES = {"summary", "education", "work", "projects", "skills", "certificates"}
TEMPLATE_IDS = {f"template-{number:02d}" for number in range(1, 4)}


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
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "操作失败，请检查配置后重试。"}})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return pages.TemplateResponse(request, "index.html", {})


@app.get("/api/templates")
async def list_templates():
    return [{"id": f"template-{n:02d}", "name": name} for n, name in enumerate(("简洁蓝", "暖色单栏", "清新绿"), 1)]


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


@app.post("/api/jobs/{job_id}/versions")
async def create_job_version(job_id: str):
    state = job_store.load()
    parent = next(((company, job) for company in state["companies"] for job in company.get("jobs", []) if job.get("id") == job_id), None)
    job = parent[1] if parent else None
    if job is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位记录不存在。"})
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
    return job_store.create_version(parent[0]["id"], job_id, content, module_order, {"template_id": "template-01", "color": "#2C5263", "page_width": "standard"})


@app.put("/api/jobs/{job_id}/versions/{version_id}")
async def update_job_version(job_id: str, version_id: str, payload: dict[str, Any]):
    try:
        state = job_store.load()
        located = next(((job, version) for company in state["companies"] for job in company.get("jobs", []) for version in job.get("versions", []) if version.get("id") == version_id and job.get("id") == job_id), None)
        if located is None:
            raise KeyError(version_id)
        content = payload.get("resume_content")
        order = payload.get("module_order")
        settings = payload.get("visual_settings")
        validate_editor_payload(content, order, settings)
        return job_store.update_version(version_id, content, order, settings)
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "not_found", "message": "岗位简历版本不存在。"}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "validation_error", "message": str(exc)}) from exc


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
    templates = {f"template-{n:02d}": ROOT / "templates" / f"template-{n:02d}.docx" for n in range(1, 4)}
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
