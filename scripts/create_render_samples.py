"""Create short, long, and empty-section DOCX/PDF QA samples from fictional fixtures."""
from __future__ import annotations

import json
import argparse
from pathlib import Path

from cvbuilder.core import check_generated_facts
from cvbuilder.templates import convert_to_pdf, fill_template

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def long_output(base):
    def entry(source, title, org, bullets):
        return {
            "source_id": source["id"], "title": title, "organization": org,
            "start_date": source.get("start_date", ""), "end_date": source.get("end_date", ""),
            "bullets": [{"text": x, "content_type": "rewrite", "source_ids": [source["id"]]} for x in bullets],
        }
    work, project, edu = base["work"][0], base["projects"][0], base["education"][0]
    output = {
        "base_resume_id": base["resume_id"], "base_resume_version": base["version"], "requirement_id": "test-job-long-001",
        "personal": {key: base["personal"][key] for key in ("name", "email", "phone", "location")},
        "summary": [{"text": base["personal"].get("summary") or "参与应用维护、脚本编写和技术文档整理。", "content_type": "rewrite", "source_ids": [work["id"]]}],
        "education": [entry(edu, edu["major"] + "（" + edu["degree"] + "）", edu["school"], edu["details"])],
        "work": [entry(work, work["title"], work["employer"], [work["description"], *work["achievements"]])],
        "projects": [entry(project, project["name"], project["role"], [project["description"], *project["achievements"]])],
        "skills": [entry(item, item["name"], item["level"], []) for item in base["skills"]],
        "certificates": [], "gaps": [], "suggestions": [],
    }
    check_generated_facts(base, output)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "sample_exports")
    output_dir = parser.parse_args().output_dir
    output_dir.mkdir(exist_ok=True)
    short = load("generated-resume.sample.json")
    long_base = load("long-resume.sample.json")
    long = long_output(long_base)
    empty = dict(short)
    empty.update({"summary": [], "education": [], "work": [], "projects": [], "skills": [], "certificates": []})
    variants = {"short": short, "long": long, "empty": empty}
    for template in sorted((ROOT / "templates").glob("template-*.docx")):
        for name, data in variants.items():
            path = output_dir / f"{name}-{template.stem}.docx"
            pdf_path = path.with_suffix(".pdf")
            if path.exists() and pdf_path.exists():
                print(f"保留已有样例: {path}")
                continue
            if path.exists() or pdf_path.exists():
                raise FileExistsError(f"样例输出不完整且目标已存在，为避免覆盖请换目录: {path}")
            fill_template(template, data, path)
            convert_to_pdf(path)
            print(path)


if __name__ == "__main__":
    main()
