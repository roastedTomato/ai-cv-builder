from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .core import check_generated_facts, check_requirement_gaps, normalize_generated_resume, populate_requirement_gaps
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


class ModelConfigurationError(RuntimeError):
    pass


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


def _request(name: str, schema_file: str, instructions: str, payload: dict[str, Any]) -> dict[str, Any]:
    client, model = _client()
    api_schema = _schema(schema_file)
    for metadata_key in ("$schema", "$id", "title"):
        api_schema.pop(metadata_key, None)
    response = client.responses.create(
        model=model,
        store=False,
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False),
        text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": api_schema}},
    )
    if not response.output_text:
        raise RuntimeError("模型未返回结构化 JSON 内容")
    try:
        result = json.loads(response.output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("模型返回内容不是有效 JSON") from exc
    errors = sorted(Draft202012Validator(_schema(schema_file)).iter_errors(result), key=lambda e: list(e.absolute_path))
    if errors:
        summary = "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in errors[:5])
        raise RuntimeError(f"模型 JSON 未通过 Schema 校验: {summary}")
    return result


def extract_requirement(text: str, requirement_id: str) -> dict[str, Any]:
    schema = "job-requirement.schema.json"
    return _request(
        "job_requirement", schema,
        "把招聘文本提取为指定 JSON。不要补全或猜测未出现的信息；公司和职位无法确定时设为 null。数组只放原文明示内容。",
        {"requirement_id": requirement_id, "job_text": text},
    )


def generate_resume(resume: dict[str, Any], requirement: dict[str, Any]) -> dict[str, Any]:
    output = _request(
        "generated_resume", "generated-resume.schema.json",
        "根据基础简历生成匹配岗位的中文简历。基础简历是唯一事实来源。只能选择、排序、压缩或改写已有事实。每条简介或要点必须提供来源 ID，并用 content_type 明确标记为 confirmed_fact（与来源原文完全一致）或 rewrite（基于来源改写）。教育条目 title 使用“专业（学历）”格式，且专业和学历必须来自基础简历。项目条目中 title 只放项目名，organization 放基础简历中的角色。没有依据的要求放入 gaps；待用户补充确认的建议放入 suggestions。不可新增数字、技能、证书、公司或经历。个人信息原样复制。",
        {"base_resume": resume, "job_requirement": requirement},
    )
    normalize_generated_resume(resume, output)
    populate_requirement_gaps(resume, requirement, output)
    check_generated_facts(resume, output)
    check_requirement_gaps(resume, requirement, output)
    return output
