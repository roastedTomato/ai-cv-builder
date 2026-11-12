from __future__ import annotations

import re
import shutil
import subprocess
import os
import tempfile
import textwrap
import glob
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Mm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ("name", "email", "phone", "location", "summary", "education", "work", "projects", "skills", "certificates")
SECTIONS = ("summary", "education", "work", "projects", "skills", "certificates")


def _apply_chinese_font(font_obj, name: str = "Hiragino Sans GB") -> None:
    font_obj.name = name
    rpr = getattr(font_obj, "_element", None)
    nested = getattr(rpr, "rPr", None)
    if nested is not None:
        rpr = nested
    if rpr is not None:
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        rfonts.set(qn("w:ascii"), name)
        rfonts.set(qn("w:hAnsi"), name)
        rfonts.set(qn("w:eastAsia"), name)


def find_soffice() -> str | None:
    """Locate LibreOffice for PDF export, including its Codex desktop bundle."""
    configured = os.environ.get("SOFFICE_PATH")
    if configured:
        explicit = Path(configured).expanduser()
        return str(explicit) if explicit.is_file() and os.access(explicit, os.X_OK) else None
    candidates = []
    candidates.extend(shutil.which(name) for name in ("soffice", "libreoffice"))
    candidates.extend([
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/Applications/LibreOfficeDev.app/Contents/MacOS/soffice",
        str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
        "/opt/homebrew/bin/soffice",
        "/usr/local/bin/soffice",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
    ])
    # Codex desktop bundles LibreOffice for document rendering; it may not be on
    # the user's shell PATH. Resolve it dynamically instead of hardcoding a version.
    candidates.extend(sorted(glob.glob(str(Path.home() / ".cache/codex-runtimes/*/dependencies/bin/override/soffice"))))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def create_templates(output_dir: Path | None = None) -> list[Path]:
    folder = output_dir or ROOT / "templates"
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for number, color, title, accent in (
        (1, "2C5263", "简历", "简洁蓝"),
        (2, "80664B", "个人简历", "暖色单栏"),
        (3, "365B48", "RESUME", "清新绿"),
    ):
        path = folder / f"template-{number:02d}.docx"
        if path.exists():
            results.append(path)
            continue
        doc = Document()
        section = doc.sections[0]
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.top_margin = Inches(0.62)
        section.bottom_margin = Inches(0.62)
        section.left_margin = Inches(0.78)
        section.right_margin = Inches(0.78)
        normal = doc.styles["Normal"]
        _apply_chinese_font(normal.font)
        normal.font.size = Pt(10)
        normal.font.color.rgb = RGBColor.from_string("333333")
        normal.paragraph_format.space_after = Pt(4)

        p = doc.add_paragraph()
        p.alignment = {1: WD_ALIGN_PARAGRAPH.LEFT, 2: WD_ALIGN_PARAGRAPH.CENTER, 3: WD_ALIGN_PARAGRAPH.RIGHT}[number]
        run = p.add_run("{{name}}")
        run.bold = True
        _apply_chinese_font(run.font)
        run.font.size = Pt(23)
        run.font.color.rgb = RGBColor.from_string(color)
        p = doc.add_paragraph()
        p.alignment = {1: WD_ALIGN_PARAGRAPH.LEFT, 2: WD_ALIGN_PARAGRAPH.CENTER, 3: WD_ALIGN_PARAGRAPH.RIGHT}[number]
        p.add_run("{{email}}  ·  {{phone}}  ·  {{location}}")
        for token in SECTIONS:
            heading = doc.add_paragraph()
            heading.paragraph_format.keep_with_next = True
            r = heading.add_run(token.upper() if token == "summary" and number == 3 else {
                "summary": "个人简介", "education": "教育经历", "work": "工作经历",
                "projects": "项目经历", "skills": "技能", "certificates": "证书"
            }[token])
            r.bold = True
            _apply_chinese_font(r.font)
            r.font.size = Pt(13)
            r.font.color.rgb = RGBColor.from_string(color)
            content = doc.add_paragraph(f"{{{{{token}}}}}")
            content.paragraph_format.keep_together = False
        doc.core_properties.title = f"第一阶段规范模板 {number}（{accent}）"
        doc.core_properties.subject = "虚构数据验证用的规范化简历模板"
        doc.save(path)
        results.append(path)
    return results


def _format_claims(claims: list[dict[str, Any]]) -> str:
    return "\n".join("• " + claim["text"] for claim in claims)


def _format_entries(items: list[dict[str, Any]], *, include_bullets: bool = True) -> str:
    rendered = []
    for item in items:
        heading = " · ".join(x for x in (item.get("title", ""), item.get("organization", "")) if x)
        dates = " - ".join(x for x in (item.get("start_date", ""), item.get("end_date", "")) if x)
        lines = [" | ".join(x for x in (heading, dates) if x)]
        if include_bullets:
            lines.extend("    " + claim["text"] for claim in item.get("bullets", []))
        rendered.append("\n".join(lines).strip())
    return "\n\n".join(x for x in rendered if x)


def resume_values(data: dict[str, Any]) -> dict[str, str]:
    personal = data["personal"]
    return {
        "name": personal["name"], "email": personal["email"], "phone": personal["phone"], "location": personal["location"],
        "summary": _format_claims(data.get("summary", [])),
        "education": _format_entries(data.get("education", [])),
        "work": _format_entries(data.get("work", [])),
        "projects": _format_entries(data.get("projects", [])),
        "skills": _format_entries(data.get("skills", []), include_bullets=False),
        "certificates": _format_entries(data.get("certificates", []), include_bullets=False),
    }


def _remove_paragraph(paragraph) -> None:
    element = paragraph._element
    element.getparent().remove(element)


def fill_template(template_path: Path, resume: dict[str, Any], output_path: Path) -> Path:
    if output_path.exists():
        raise FileExistsError(f"目标文件已存在，为避免覆盖请使用新路径: {output_path}")
    doc = Document(template_path)
    values = resume_values(resume)
    section_heading = {"summary": "个人简介", "education": "教育经历", "work": "工作经历", "projects": "项目经历", "skills": "技能", "certificates": "证书", "SUMMARY": "SUMMARY"}
    # Empty modules remove their heading and content paragraph.
    paras = list(doc.paragraphs)
    for index, paragraph in enumerate(paras):
        token_match = re.fullmatch(r"\{\{(summary|education|work|projects|skills|certificates)\}\}", paragraph.text.strip())
        if token_match and not values[token_match.group(1)]:
            _remove_paragraph(paragraph)
            if index and paras[index - 1].text.strip() in set(section_heading.values()):
                _remove_paragraph(paras[index - 1])
    for paragraph in doc.paragraphs:
        for token, value in values.items():
            needle = "{{" + token + "}}"
            if needle in paragraph.text:
                paragraph.text = paragraph.text.replace(needle, value)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    with zipfile.ZipFile(output_path) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"生成的 DOCX 包损坏: {corrupt}")
        all_text = "\n".join(archive.read(name).decode("utf-8", "ignore") for name in archive.namelist() if name.endswith(".xml"))
        if re.search(r"\{\{[^}]+\}\}", all_text):
            raise RuntimeError("导出 DOCX 中仍有未替换的占位符")
    return output_path


def fill_template_text(template_path: Path, body_text: str, output_path: Path) -> Path:
    """Fill a normalized template with user-edited plain resume text."""
    if output_path.exists():
        raise FileExistsError(f"目标文件已存在，为避免覆盖请使用新路径: {output_path}")
    lines = body_text.strip().splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
        raise ValueError("简历正文至少需要姓名和联系方式两行")
    doc = Document(template_path)
    headings = {"个人简介", "教育经历", "工作经历", "项目经历", "技能", "证书", "SUMMARY"}
    def set_text(paragraph, value: str) -> None:
        if paragraph.runs:
            paragraph.runs[0].text = value
            for run in paragraph.runs[1:]:
                run.text = ""
        else:
            paragraph.add_run(value)
    for paragraph in list(doc.paragraphs):
        content = paragraph.text.strip()
        if content == "{{name}}":
            set_text(paragraph, lines[0].strip())
        elif "{{email}}" in content:
            set_text(paragraph, lines[1].strip())
        elif content == "{{summary}}":
            set_text(paragraph, "\n".join(lines[2:]).strip())
        elif content in headings or re.fullmatch(r"\{\{(?:education|work|projects|skills|certificates)\}\}", content):
            _remove_paragraph(paragraph)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    with zipfile.ZipFile(output_path) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"生成的 DOCX 包损坏: {corrupt}")
        all_text = "\n".join(archive.read(name).decode("utf-8", "ignore") for name in archive.namelist() if name.endswith(".xml"))
        if re.search(r"\{\{[^}]+\}\}", all_text):
            raise RuntimeError("导出 DOCX 中仍有未替换的占位符")
    return output_path


def _modular_section_text(key: str, value: Any) -> str:
    if key == "summary":
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n".join(str(item.get("text", "")).strip() if isinstance(item, dict) else str(item).strip() for item in value if item)
    if not isinstance(value, list):
        return str(value).strip() if isinstance(value, str) else ""
    rendered = []
    for item in value:
        if isinstance(item, str):
            if item.strip(): rendered.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "").strip()
        organization = str(item.get("organization", "") or "").strip()
        dates = " - ".join(str(item.get(key, "")).strip() for key in ("start_date", "end_date") if item.get(key))
        heading = " · ".join(part for part in (title, organization, dates) if part)
        bullets = item.get("bullets", [])
        lines = ["• " + str(b.get("text", "")).strip() for b in bullets if isinstance(b, dict) and str(b.get("text", "")).strip()] if isinstance(bullets, list) else []
        rendered.append("\n".join(([heading] if heading else []) + lines).strip())
    return "\n\n".join(item for item in rendered if item)


def fill_template_modular(template_path: Path, content: dict[str, Any], module_order: list[str], visual_settings: dict[str, Any], output_path: Path) -> Path:
    """Render the editable generated-resume sections in the user's selected order."""
    if output_path.exists():
        raise FileExistsError(f"目标文件已存在，为避免覆盖请使用新路径: {output_path}")
    doc = Document(template_path)
    section_labels = {"summary": "个人简介", "education": "教育经历", "work": "工作经历", "projects": "项目经历", "skills": "技能", "certificates": "证书"}
    paragraphs = list(doc.paragraphs)
    values = {
        "name": str(content.get("personal", {}).get("name", "")) if isinstance(content.get("personal"), dict) else "",
        "contact": "  ·  ".join(str(content.get("personal", {}).get(key, "")).strip() for key in ("email", "phone", "location") if content.get("personal", {}).get(key)) if isinstance(content.get("personal"), dict) else "",
    }
    name_p = next((p for p in paragraphs if p.text.strip() == "{{name}}"), None)
    contact_p = next((p for p in paragraphs if "{{email}}" in p.text), None)
    if name_p is not None: name_p.text = values["name"]
    if contact_p is not None: contact_p.text = values["contact"]
    groups = {}
    for key, label in section_labels.items():
        token = "{{" + key + "}}"
        heading = next((p for p in paragraphs if p.text.strip() == label or (key == "summary" and p.text.strip() == "SUMMARY")), None)
        body = next((p for p in paragraphs if p.text.strip() == token), None)
        text = _modular_section_text(key, content.get(key))
        if not text or heading is None or body is None:
            if heading is not None: _remove_paragraph(heading)
            if body is not None: _remove_paragraph(body)
            continue
        body.text = text
        groups[key] = (heading, body)
    anchor = contact_p or name_p
    if anchor is not None:
        anchor_element = anchor._p
        for element in (p._element for group in groups.values() for p in group):
            element.getparent().remove(element)
        for key in module_order:
            if key in groups:
                for paragraph in groups[key]:
                    anchor_element.addnext(deepcopy(paragraph._p))
                    anchor_element = anchor_element.getnext()
    accent = str(visual_settings.get("color", visual_settings.get("accent_color", "#2C5263"))).lstrip("#")
    if re.fullmatch(r"[0-9A-Fa-f]{6}", accent):
        for paragraph in doc.paragraphs:
            if paragraph.text.strip() == values["name"] or paragraph.text.strip() in section_labels.values() or paragraph.text.strip() == "SUMMARY":
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor.from_string(accent.upper())
    width_setting = visual_settings.get("page_width", "standard")
    widths = {"narrow": 180, "standard": 210, "wide": 240}
    width = widths.get(width_setting, width_setting)
    if isinstance(width, (int, float)) and 170 <= width <= 250:
        for section in doc.sections:
            section.page_width = Mm(width)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    with zipfile.ZipFile(output_path) as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"生成的 DOCX 包损坏: {corrupt}")
        all_text = "\n".join(archive.read(name).decode("utf-8", "ignore") for name in archive.namelist() if name.endswith(".xml"))
        if re.search(r"\{\{[^}]+\}\}", all_text):
            raise RuntimeError("导出 DOCX 中仍有未替换的占位符")
    return output_path


def convert_to_pdf(docx_path: Path) -> Path:
    soffice = find_soffice()
    if not soffice:
        if os.environ.get("SOFFICE_PATH"):
            raise RuntimeError("SOFFICE_PATH 指定位置不存在或不可执行；请设置为 LibreOffice 的 soffice 可执行文件")
        raise RuntimeError("未找到 LibreOffice（soffice）。请安装 LibreOffice，或设置 SOFFICE_PATH；无法导出 PDF")
    pdf_path = docx_path.with_suffix(".pdf")
    if pdf_path.exists():
        raise FileExistsError(f"目标 PDF 已存在，为避免覆盖请先选择新输出路径: {pdf_path}")
    with tempfile.TemporaryDirectory(prefix="cvbuilder-fonts-") as cache:
        config = Path(cache) / "fonts.conf"
        config.write_text(textwrap.dedent(f"""\
            <?xml version="1.0"?>
            <!DOCTYPE fontconfig SYSTEM "fonts.dtd">
            <fontconfig>
              <dir>/System/Library/Fonts</dir>
              <dir>/Library/Fonts</dir>
              <dir>/usr/share/fonts</dir>
              <dir>/usr/local/share/fonts</dir>
              <cachedir>{cache}/font-cache</cachedir>
            </fontconfig>
        """), encoding="utf-8")
        env = os.environ.copy()
        env["FONTCONFIG_FILE"] = str(config)
        env["XDG_CACHE_HOME"] = cache
        completed = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
            capture_output=True, text=True, timeout=120, env=env,
        )
    if completed.returncode != 0 or not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"PDF 导出失败: {(completed.stderr or completed.stdout).strip()}")
    return pdf_path
