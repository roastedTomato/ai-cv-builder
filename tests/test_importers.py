import tempfile
import unittest
from pathlib import Path

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

