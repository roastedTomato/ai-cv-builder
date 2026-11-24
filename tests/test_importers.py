import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from cvbuilder.importers import EmptyDocumentError, InvalidDocumentError, UnsupportedFormatError, extract_text
from cvbuilder.importers.fields import extract_personal, redact_personal, keep_explicit_skills


class ImporterTests(unittest.TestCase):
    def test_local_contacts_and_explicit_skills_only(self):
        text = "姓名：李小明\n邮箱：li.test@example.com\n电话：13800138000\n工作经历\n曾使用 Tableau\n技能/技术栈\nPython、SQL、Python\n教育经历\n沟通能力强"
        personal = extract_personal(text)
        self.assertEqual(personal["name"], "李小明")
        self.assertEqual(personal["email"], "li.test@example.com")
        self.assertEqual(personal["phone"], "13800138000")
        redacted = redact_personal(text, personal)
        for private in ("李小明", "li.test@example.com", "13800138000"):
            self.assertNotIn(private, redacted)
        draft = {"skills": [{"name": name} for name in ("Python", "SQL", "Python", "Tableau", "沟通能力强")]}
        keep_explicit_skills(draft, text)
        self.assertEqual([item["name"] for item in draft["skills"]], ["Python", "SQL"])

    def test_extracts_docx_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fictional-test.docx"
            doc = Document(); doc.add_paragraph("虚构测试数据：数据分析项目")
            doc.save(path)
            result = extract_text(path)
            self.assertEqual(result.format, "docx")
            self.assertIn("虚构测试数据", result.text)

    def test_keeps_docx_table_rows_under_their_original_headings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fictional-test.docx"
            doc = Document()
            doc.add_paragraph("教育经历")
            table = doc.add_table(rows=0, cols=3)
            for values in (
                ("学校", "学历", "专业"),
                ("虚构星河大学", "本科", "信息管理"),
            ):
                for cell, value in zip(table.add_row().cells, values):
                    cell.text = value
            doc.add_paragraph("工作经历")
            cells = doc.add_table(rows=1, cols=2).rows[0].cells
            cells[0].text = "公司：虚构星河科技"
            cells[1].text = "职位：数据分析师"
            doc.add_paragraph("技能")
            doc.add_paragraph("Python")
            doc.save(path)
            self.assertEqual(
                extract_text(path).text,
                "教育经历\n学校\t学历\t专业\n虚构星河大学\t本科\t信息管理\n"
                "工作经历\n公司：虚构星河科技\t职位：数据分析师\n技能\nPython",
            )

    def test_explicit_skills_keep_numbered_names_and_stop_at_inline_next_section(self):
        text = "专业技能：\n1. Python / SQL\n3D 建模、CI/CD\n工作经历：使用 Tableau\nExcel\n技术栈：Git"
        draft = {"skills": [{"name": name} for name in ("Python", "SQL", "3D 建模", "CI/CD", "Excel", "Git")]}
        keep_explicit_skills(draft, text)
        self.assertEqual([item["name"] for item in draft["skills"]], ["Python", "SQL", "3D 建模", "CI/CD", "Git"])

    def test_scanned_or_empty_pdf_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "empty.pdf"
            path.write_bytes(b"%PDF-1.4\n%%EOF")
            with self.assertRaises((EmptyDocumentError, InvalidDocumentError)):
                extract_text(path)

    def test_unsupported_extension_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.txt"; path.write_text("虚构测试数据")
            with self.assertRaises(UnsupportedFormatError):
                extract_text(path)

    def test_invalid_docx_is_classified(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.docx"; path.write_text("not a document")
            with self.assertRaises(InvalidDocumentError):
                extract_text(path)

    def test_legacy_doc_uses_configured_libreoffice_location(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fictional-test.doc"
            path.write_bytes(b"fictional test document")

            def convert(command, **_kwargs):
                (Path(command[command.index("--outdir") + 1]) / "fictional-test.txt").write_text(
                    "虚构测试招聘要求", encoding="utf-8")

            with patch("cvbuilder.templates.find_soffice", return_value="/fictional/soffice") as locate, \
                 patch("cvbuilder.importers.subprocess.run", side_effect=convert) as run:
                result = extract_text(path)
            locate.assert_called_once_with()
            self.assertEqual(run.call_args.args[0][0], "/fictional/soffice")
            self.assertEqual(result.text, "虚构测试招聘要求")

