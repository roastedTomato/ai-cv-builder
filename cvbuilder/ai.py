from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .core import check_generated_facts, check_requirement_gaps, normalize_generated_resume, populate_requirement_gaps, source_map
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


class ModelConfigurationError(RuntimeError):
    pass


class ModelResponseError(RuntimeError):
    """Model returned output that cannot be used by the application."""
    code = "invalid_model_response"


class ModelSchemaValidationError(ModelResponseError):
    """Model output violated the full local schema after API validation."""
    code = "local_schema_validation"

    def __init__(self, errors: list[Any]):
        self.fields = [".".join(map(str, error.absolute_path)) or "$" for error in errors[:5]]
        self.validators = [str(error.validator) for error in errors[:5]]
        self.missing = [sorted(set(error.validator_value) - set(error.instance)) if error.validator == "required" and isinstance(error.instance, dict) else [] for error in errors[:5]]
        super().__init__("模型 JSON 未通过本地 Schema 校验")


def _client():
    if not os.environ.get("OPENAI_API_KEY"):
        raise ModelConfigurationError("请设置 OPENAI_API_KEY 环境变量。")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ModelConfigurationError("缺少 openai 依赖，请先运行 `uv sync`。") from exc
    return OpenAI(max_retries=1, timeout=60.0), model


def _schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "docs" / "schemas" / name).read_text(encoding="utf-8"))


def _structured_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert our Draft 2020-12 schemas to the stricter API-supported subset.

    Constraints such as const/minLength/minItems remain enforced by the local
    Draft202012Validator after the response. Structured Outputs requires every
    object to disallow additional properties and every property to be required.
    """
    unsupported = {"$schema", "$id", "title", "const", "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "pattern", "format"}

    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        original_props = node.get("properties", {})
        original_required = set(node.get("required", []))
        result = {key: clean(value) for key, value in node.items() if key not in unsupported}
        if result.get("type") == "object" or "properties" in result:
            props = result.get("properties", {})
            result["additionalProperties"] = False
            if props:
                # fixture_note is test-only metadata with an exact-value const;
                # it must remain absent from user resume data and the API schema.
                props.pop("fixture_note", None)
                for key, prop_schema in props.items():
                    if key not in original_required:
                        prop_type = prop_schema.get("type")
                        if isinstance(prop_type, str):
                            prop_schema["type"] = [prop_type, "null"]
                        elif isinstance(prop_type, list) and "null" not in prop_type:
                            prop_schema["type"] = [*prop_type, "null"]
                result["required"] = list(props)
        return result

    return clean(schema)


def _restore_optional_fields(data: Any, schema: dict[str, Any]) -> Any:
    """Drop null placeholders used to express optional fields to the API."""
    definitions = schema.get("$defs", {})

    def walk(value: Any, node: dict[str, Any]) -> Any:
        ref = node.get("$ref")
        if ref and ref.startswith("#/$defs/"):
            node = definitions.get(ref.rsplit("/", 1)[-1], node)
        if isinstance(value, list):
            item_schema = node.get("items", {})
            return [walk(item, item_schema) if isinstance(item_schema, dict) else item for item in value]
        if not isinstance(value, dict):
            return value
        props = node.get("properties", {})
        required = set(node.get("required", []))
        for key in list(value):
            child_schema = props.get(key, {})
            if key not in required and value[key] is None:
                value.pop(key)
            elif isinstance(child_schema, dict):
                value[key] = walk(value[key], child_schema)
        return value

    return walk(data, schema)


def _complete_import_draft(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Represent unknown resume facts as blanks for the user's review."""
    definitions = schema.get("$defs", {})

    def resolve(node: dict[str, Any]) -> dict[str, Any]:
        ref = node.get("$ref")
        return definitions[ref.rsplit("/", 1)[-1]] if ref and ref.startswith("#/$defs/") else node

    def blank(node: dict[str, Any]) -> Any:
        kind = resolve(node).get("type")
        if kind == "array":
            return []
        if kind == "object":
            return {}
        return ""

    def fill(value: Any, node: dict[str, Any]) -> Any:
        node = resolve(node)
        if isinstance(value, list) and node.get("type") == "array":
            return [fill(item, node.get("items", {})) for item in value]
        if isinstance(value, dict) and node.get("type") == "object":
            props = node.get("properties", {})
            for key in node.get("required", []):
                if key not in value or value[key] is None:
                    value[key] = blank(props[key])
            for key, child in props.items():
                if key in value:
                    value[key] = fill(value[key], child)
        return value

    return fill(data, schema)


def _complete_generated_draft(data: dict[str, Any], resume: dict[str, Any], requirement: dict[str, Any]) -> dict[str, Any]:
    """Fill omitted display fields only when a confirmed source supplies them."""
    data.setdefault("base_resume_id", resume["resume_id"])
    data.setdefault("base_resume_version", resume["version"])
    data.setdefault("requirement_id", requirement["requirement_id"])
    data.setdefault("personal", {key: resume["personal"].get(key, "") for key in ("name", "email", "phone", "location")})
    for field in ("summary", "education", "work", "projects", "skills", "certificates", "gaps", "suggestions"):
        data.setdefault(field, [])
    sources = source_map(resume)
    for section in ("education", "work", "projects", "skills", "certificates"):
        entries = data.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_info = sources.get(entry.get("source_id"))
            if source_info is None:
                continue
            source_section, source = source_info
            if section == "education" and source_section == "education":
                major, degree = source.get("major", ""), source.get("degree", "")
                title = f"{major}（{degree}）" if major and degree else major or degree
                organization = source.get("school", "")
            elif section == "work" and source_section == "work":
                title, organization = source.get("title", ""), source.get("employer", "")
            elif section == "projects" and source_section == "projects":
                title, organization = source.get("name", ""), source.get("role", "")
            elif section == "skills" and source_section == "skills":
                title, organization = source.get("name", ""), source.get("level", "")
            elif section == "skills" and source_section == "projects" and len(source.get("technologies", [])) == 1:
                title, organization = source["technologies"][0], ""
            elif section == "certificates" and source_section == "certificates":
                title, organization = source.get("name", ""), source.get("issuer", "")
            else:
                continue
            # These fields are fixed source facts, not text for the model to
            # paraphrase. Canonicalize even when the model supplied a value.
            entry["title"] = title
            entry["organization"] = organization
            entry["start_date"] = source.get("start_date", "") if section not in ("skills", "certificates") else ""
            entry["end_date"] = source.get("end_date", "") if section not in ("skills", "certificates") else ""
            entry.setdefault("bullets", [])
    return data


def _request(name: str, schema_file: str, instructions: str, payload: dict[str, Any], *, validate_result: bool = True) -> dict[str, Any]:
    client, model = _client()
    api_schema = _structured_output_schema(_schema(schema_file))
    response = client.responses.create(
        model=model,
        store=False,
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False),
        text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": api_schema}},
    )
    if not response.output_text:
        raise ModelResponseError("模型未返回结构化 JSON 内容")
    try:
        result = json.loads(response.output_text)
    except json.JSONDecodeError as exc:
        raise ModelResponseError("模型返回内容不是有效 JSON") from exc
    original_schema = _schema(schema_file)
    result = _restore_optional_fields(result, original_schema)
    if name == "base_resume" and isinstance(result, dict):
        # Identifiers are local metadata, not claims extracted from a resume.
        result.pop("fixture_note", None)
        result = _complete_import_draft(result, original_schema)
        result["resume_id"] = "resume-" + uuid.uuid4().hex
        result["version"] = "1"
        for section in ("education", "work", "projects", "skills", "certificates"):
            items = result.get(section)
            if isinstance(items, list):
                for index, item in enumerate(items, 1):
                    if isinstance(item, dict):
                        item["id"] = f"{section}-{index:03d}"
    elif name == "generated_resume" and isinstance(result, dict):
        result = _complete_generated_draft(result, payload["base_resume"], payload["job_requirement"])
    if validate_result:
        errors = sorted(Draft202012Validator(original_schema).iter_errors(result), key=lambda e: list(e.absolute_path))
        if errors:
            raise ModelSchemaValidationError(errors)
    return result


def extract_requirement(text: str, requirement_id: str) -> dict[str, Any]:
    schema = "job-requirement.schema.json"
    return _request(
        "job_requirement", schema,
        "把招聘文本提取为指定 JSON。不要补全或猜测未出现的信息；公司和职位无法确定时设为 null。数组只放原文明示内容。",
        {"requirement_id": requirement_id, "job_text": text},
    )


def structure_resume(text: str, personal: dict[str, str]) -> dict[str, Any]:
    """Turn locally extracted document text into an explicitly unconfirmed draft."""
    return _request(
        "base_resume", "base-resume.schema.json",
        "将简历原文整理到基础简历 JSON。只填写原文明确支持的信息；无法确定的字段置空或空数组。不得新增经历、技能、证书或数字。技能只从明确标为技能或技术栈的栏目提取，不从工作、项目或自我评价推断，重复技能只保留一次。为经历生成稳定且互不重复的虚构格式 ID。",
        {"resume_id": "resume-local", "version": "1", "personal": personal, "resume_text": text},
    )


GENERATE_INSTRUCTIONS = "根据基础简历生成匹配岗位的中文简历。基础简历是唯一事实来源。只能选择、排序、压缩或改写已有事实。每条简介或要点必须提供来源 ID，并用 content_type 明确标记为 confirmed_fact（与来源原文完全一致）或 rewrite（基于来源改写）。教育条目 title 使用“专业（学历）”格式，且专业和学历必须来自基础简历。项目条目中 title 只放项目名，organization 放基础简历中的角色。没有依据的要求放入 gaps；待用户补充确认的建议放入 suggestions。不可新增数字、技能、证书、公司或经历。个人信息原样复制。"


def _model_resume_without_contacts(resume: dict[str, Any]) -> dict[str, Any]:
    model_resume = deepcopy(resume)
    for key in ("name", "email", "phone", "location"):
        model_resume["personal"][key] = ""
    return model_resume


def generate_resume(resume: dict[str, Any], requirement: dict[str, Any]) -> dict[str, Any]:
    output = _request(
        "generated_resume", "generated-resume.schema.json",
        GENERATE_INSTRUCTIONS,
        {"base_resume": _model_resume_without_contacts(resume), "job_requirement": requirement},
    )
    output["personal"] = {key: resume["personal"].get(key, "") for key in ("name", "email", "phone", "location")}
    normalize_generated_resume(resume, output)
    populate_requirement_gaps(resume, requirement, output)
    check_generated_facts(resume, output)
    check_requirement_gaps(resume, requirement, output)
    return output


def generate_resume_unchecked(resume: dict[str, Any], requirement: dict[str, Any]) -> dict[str, Any]:
    """Return a web editing draft without local Schema or fact gates."""
    output = _request(
        "generated_resume", "generated-resume.schema.json",
        GENERATE_INSTRUCTIONS,
        {"base_resume": resume, "job_requirement": requirement},
        validate_result=False,
    )
    output["personal"] = {key: resume["personal"].get(key, "") for key in ("name", "email", "phone", "location")}
    output["gaps"] = []
    return output


def generate_resume_from_job_unchecked(resume: dict[str, Any], job_text: str) -> dict[str, Any]:
    """Generate a web editing draft in one request using the raw job posting."""
    requirement = {"requirement_id": "job-" + uuid.uuid4().hex, "job_text": job_text}
    output = _request(
        "generated_resume", "generated-resume.schema.json",
        GENERATE_INSTRUCTIONS,
        {"base_resume": resume, "job_requirement": requirement},
        validate_result=False,
    )
    output["personal"] = {key: resume["personal"].get(key, "") for key in ("name", "email", "phone", "location")}
    output["gaps"] = []
    return output
