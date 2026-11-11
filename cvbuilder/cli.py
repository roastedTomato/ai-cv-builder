from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .ai import ModelConfigurationError, extract_requirement, generate_resume
from .core import analyze_match, check_generated_facts
from .templates import convert_to_pdf, create_templates, fill_template

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = {
    "base": ROOT / "docs/schemas/base-resume.schema.json",
    "job": ROOT / "docs/schemas/job-requirement.schema.json",
    "generated": ROOT / "docs/schemas/generated-resume.schema.json",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate(data: Any, kind: str) -> None:
    schema = read_json(SCHEMAS[kind])
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in errors)
        raise ValueError(f"{kind} Schema 校验失败: {details}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cvbuilder", description="第一阶段简历生成与模板导出 CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate", help="校验 JSON Schema")
    p.add_argument("kind", choices=SCHEMAS)
    p.add_argument("input", type=Path)
    p = sub.add_parser("parse-job", help="使用 OpenAI 模型结构化提取招聘要求")
    p.add_argument("input", type=Path)
    p.add_argument("--id", required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("match", help="按招聘要求关键词分析基础简历匹配与能力缺口")
    p.add_argument("resume", type=Path)
    p.add_argument("requirement", type=Path)
    p.add_argument("--output", type=Path)
    p = sub.add_parser("generate", help="使用 OpenAI 模型生成有来源追溯的岗位简历")
    p.add_argument("resume", type=Path)
    p.add_argument("requirement", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("make-templates", help="生成三份规范化实验模板")
    p.add_argument("--output-dir", type=Path, default=ROOT / "templates")
    p = sub.add_parser("export", help="填充模板并导出 DOCX 与 PDF")
    p.add_argument("resume", type=Path)
    p.add_argument("--template", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="目标 .docx 路径")
    p = sub.add_parser("demo", help="使用样例数据完整执行模型生成和三模板导出")
    p.add_argument("--output-dir", type=Path, default=ROOT / "sample_exports")

    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            validate(read_json(args.input), args.kind)
            print(f"{args.input}: {args.kind} Schema 校验通过")
        elif args.command == "parse-job":
            output = extract_requirement(args.input.read_text(encoding="utf-8"), args.id)
            validate(output, "job")
            write_json(output, args.output)
        elif args.command == "match":
            result = analyze_match(read_json(args.resume), read_json(args.requirement))
            rendered = json.dumps(result, ensure_ascii=False, indent=2)
            if args.output:
                args.output.write_text(rendered + "\n", encoding="utf-8")
            else:
                print(rendered)
        elif args.command == "generate":
            base, req = read_json(args.resume), read_json(args.requirement)
            validate(base, "base")
            validate(req, "job")
            result = generate_resume(base, req)
            validate(result, "generated")
            write_json(result, args.output)
        elif args.command == "make-templates":
            for path in create_templates(args.output_dir):
                print(path)
        elif args.command == "export":
            result = read_json(args.resume)
            validate(result, "generated")
            fill_template(args.template, result, args.output)
            print(args.output)
            print(convert_to_pdf(args.output))
        elif args.command == "demo":
            base_path = ROOT / "fixtures/base-resume.sample.json"
            req_text_path = ROOT / "fixtures/job-requirement.sample.txt"
            base = read_json(base_path)
            validate(base, "base")
            args.output_dir.mkdir(parents=True, exist_ok=True)
            req = extract_requirement(req_text_path.read_text(encoding="utf-8"), "test-job-001")
            validate(req, "job")
            write_json(req, args.output_dir / "job-requirement.sample.json")
            match = analyze_match(base, req)
            write_json(match, args.output_dir / "match-analysis.sample.json")
            generated = generate_resume(base, req)
            validate(generated, "generated")
            check_generated_facts(base, generated)
            json_path = args.output_dir / "generated-resume.sample.json"
            write_json(generated, json_path)
            templates = sorted((ROOT / "templates").glob("*.docx"))
            if len(templates) != 3:
                templates = create_templates()
            for template in templates:
                docx_path = args.output_dir / f"{template.stem}.docx"
                fill_template(template, generated, docx_path)
                convert_to_pdf(docx_path)
            print(f"演示流程完成: {args.output_dir}")
    except (OSError, ValueError, RuntimeError, ModelConfigurationError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # SDK error bodies can echo credential fragments; report metadata only.
        status = getattr(exc, "status_code", None)
        code = getattr(exc, "code", None)
        metadata = ", ".join(str(value) for value in (status, code) if value is not None)
        suffix = f" ({metadata})" if metadata else ""
        print(f"模型或导出操作失败: {type(exc).__name__}{suffix}。请检查 API 凭据、模型权限、网络或 LibreOffice 配置。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
