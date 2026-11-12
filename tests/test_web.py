import unittest
from pathlib import Path
from unittest.mock import patch
import importlib.util
import sys

from cvbuilder.cli import ROOT, read_json

WEB_AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "jinja2", "multipart"))


@unittest.skipUnless(WEB_AVAILABLE, "Web dependencies are not installed in this offline environment")
class WebContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cvbuilder import web
        cls.web = web


    def test_api_contract_is_documented_and_loopback_launch_is_fixed(self):
        source = Path(self.web.__file__).read_text(encoding="utf-8")
        self.assertIn("POST /api/import/{id}/confirm", source)
        self.assertIn('host="127.0.0.1"', source)
        self.assertIn("store=False", Path(self.web.ai.__file__).read_text(encoding="utf-8"))

    def test_base_resume_writes_are_schema_validated(self):
        resume = read_json(ROOT / "fixtures/base-resume.sample.json")
        with patch.object(self.web.store, "save") as save:
            import asyncio
            asyncio.run(self.web.put_resume(resume))
            save.assert_called_once_with(resume)
        invalid = {"unexpected": True}
        with self.assertRaises(Exception):
            import asyncio
            asyncio.run(self.web.put_resume(invalid))

    def test_required_base_fields_block_direct_save_and_import_confirmation(self):
        from fastapi.testclient import TestClient
        from copy import deepcopy
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        valid = read_json(ROOT / "fixtures/base-resume.sample.json")
        invalid = deepcopy(valid)
        invalid["personal"]["name"] = "  "
        invalid["personal"]["email"] = ""
        invalid["personal"]["phone"] = ""
        invalid["education"][0]["major"] = ""
        invalid["work"][0]["employer"] = ""
        invalid["projects"][0]["role"] = ""
        invalid["skills"][0]["name"] = ""
        with patch.object(self.web.store, "save") as save:
            direct = client.put("/api/resume", json=invalid)
            self.web.drafts["fictional-draft-test"] = valid
            try:
                imported = client.post("/api/import/fictional-draft-test/confirm", json=invalid)
                self.assertEqual(imported.status_code, 422)
                self.assertEqual(imported.json()["error"]["code"], "required_fields")
                self.assertIn("专业", imported.json()["error"]["message"])
                self.assertIn("fictional-draft-test", self.web.drafts)
                self.assertEqual(client.post("/api/import/fictional-draft-test/confirm", json=valid).status_code, 200)
            finally:
                self.web.drafts.pop("fictional-draft-test", None)
            self.assertEqual(direct.status_code, 422)
            self.assertEqual(save.call_count, 1)

    def test_generate_requires_job_text_and_returns_resume_directly(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        base = read_json(ROOT / "fixtures/base-resume.sample.json")
        draft = {"summary": [{"text": "虚构测试：可编辑的简历正文"}]}
        with patch.object(self.web.store, "load", return_value=base), patch.object(self.web.ai, "generate_resume_from_job_unchecked", return_value=draft) as generate:
            missing = client.post("/api/generate", json={"job_text": ""})
            self.assertEqual(missing.status_code, 422)
            self.assertEqual(missing.json()["error"]["code"], "validation_error")
            response = client.post("/api/generate", json={"job_text": "虚构测试岗位要求"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), draft)
            generate.assert_called_once_with(base, "虚构测试岗位要求")
        page = client.get("/").text
        self.assertIn("未经事实核验", page)
        self.assertNotIn("确认分析内容", page)
        self.assertIn("确认生成简历", page)
        self.assertIn("resume-document", page)
        self.assertIn('id="generation-progress"', page)

    def test_full_local_flow_persists_base_then_resets(self):
        from docx import Document
        from fastapi.testclient import TestClient
        from tempfile import TemporaryDirectory
        from cvbuilder.storage import ResumeStore
        from cvbuilder.job_storage import JobRecordStore
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        base = read_json(ROOT / "fixtures/base-resume.sample.json")
        generated = read_json(ROOT / "fixtures/generated-resume.sample.json")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fictional.docx"
            doc = Document()
            doc.add_paragraph("姓名：李小明")
            doc.add_paragraph("邮箱：li.test@example.com")
            doc.add_paragraph("电话：13800138000")
            doc.save(path)
            with patch.object(self.web, "store", ResumeStore(Path(folder) / "appdata")), patch.object(self.web, "job_store", JobRecordStore(Path(folder) / "appdata")), patch.object(self.web.ai, "structure_resume", return_value=base), patch.object(self.web.ai, "generate_resume_from_job_unchecked", return_value=generated):
                imported = client.post("/api/import", files={"file": ("fictional.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}).json()
                self.assertIsNone(client.get("/api/resume").json())
                self.assertEqual(client.post(f"/api/import/{imported['draft_id']}/confirm", json=imported["draft"]).status_code, 200)
                self.assertEqual(ResumeStore(Path(folder) / "appdata").load(), imported["draft"])
                self.assertEqual(client.post("/api/generate", json={"job_text": "虚构测试岗位"}).status_code, 200)
                self.assertEqual(client.post("/api/export", json={"template_id": "template-01", "format": "docx", "resume": generated}).status_code, 200)
                self.assertEqual(client.post("/api/reset").status_code, 200)
                self.assertIsNone(client.get("/api/resume").json())
                self.assertEqual(client.post("/api/generate", json={"job_text": "虚构测试岗位"}).status_code, 409)

    def test_job_versions_generate_preserve_and_update_in_place(self):
        from fastapi.testclient import TestClient
        from tempfile import TemporaryDirectory
        from cvbuilder.job_storage import JobRecordStore
        from cvbuilder.storage import ResumeStore
        base = read_json(ROOT / "fixtures/base-resume.sample.json")
        generated = read_json(ROOT / "fixtures/generated-resume.sample.json")
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        with TemporaryDirectory() as folder:
            records = JobRecordStore(Path(folder))
            base_store = ResumeStore(Path(folder) / "base-data")
            base_store.save(base)
            with patch.object(self.web, "job_store", records), patch.object(self.web, "store", base_store), patch.object(self.web.ai, "generate_resume_from_job_unchecked", return_value=generated) as generate:
                company = client.post("/api/companies", json={"name": "虚构测试公司"}).json()
                job = client.post(f"/api/companies/{company['id']}/jobs", json={"title": "虚构测试职位", "job_text": "虚构测试招聘要求"}).json()
                first = client.post(f"/api/jobs/{job['id']}/versions", json={}).json()
                second = client.post(f"/api/jobs/{job['id']}/versions", json={}).json()
                self.assertEqual([first["number"], second["number"]], [1, 2])
                self.assertEqual(first["job_text"], "虚构测试招聘要求")
                self.assertEqual(generate.call_count, 2)
                edited_content = {**generated, "summary": [{"text": "虚构测试：用户编辑版本一"}]}
                updated = client.put(f"/api/jobs/{job['id']}/versions/{first['id']}", json={"resume_content": edited_content, "module_order": first["module_order"], "visual_settings": {"template_id": "template-02", "color": "#80664B", "page_width": "wide"}})
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["number"], 1)
                self.assertEqual(updated.json()["resume_content"]["summary"][0]["text"], "虚构测试：用户编辑版本一")
                restored = JobRecordStore(Path(folder)).load()["companies"][0]["jobs"][0]["versions"]
                self.assertEqual([version["number"] for version in restored], [1, 2])
                self.assertEqual(restored[1]["resume_content"], generated)
                reset = client.post("/api/reset")
                self.assertEqual(reset.status_code, 200)
                self.assertEqual(records.load(), {"companies": []})

    def test_modular_export_honors_requested_order_and_settings(self):
        from fastapi.testclient import TestClient
        from docx import Document
        from io import BytesIO
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        content = {"personal": {"name": "虚构测试候选人", "email": "test@example.invalid", "phone": "000", "location": "测试市"}, "summary": [{"text": "虚构测试简介", "content_type": "rewrite", "source_ids": ["test"]}], "work": [{"title": "虚构测试职位", "organization": "虚构测试公司", "start_date": "2022", "end_date": "2023", "bullets": [{"text": "虚构测试经历", "content_type": "rewrite", "source_ids": ["test"]}]}], "education": []}
        response = client.post("/api/export", json={"template_id": "template-01", "format": "docx", "resume_content": content, "module_order": ["work", "summary"], "visual_settings": {"template_id": "template-01", "color": "#16766D", "page_width": "wide"}})
        self.assertEqual(response.status_code, 200)
        paragraphs = [p.text for p in Document(BytesIO(response.content)).paragraphs]
        self.assertLess(paragraphs.index("工作经历"), paragraphs.index("个人简介"))
        self.assertIn("虚构测试经历", "\n".join(paragraphs))
        self.assertNotIn("{{", "\n".join(paragraphs))

    def test_http_page_templates_validation_and_loopback_host(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("简历工作台", page.text)
        self.assertIn('id="resume-document"', page.text)
        self.assertIn('id="company-list"', page.text)
        self.assertIn('id="page-width"', page.text)
        self.assertIn('id="base-progress"', page.text)
        self.assertNotIn('id="generated"', page.text)
        self.assertEqual(len(client.get("/api/templates").json()), 3)
        invalid = client.put("/api/resume", json={"not": "a resume"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error"]["code"], "validation_error")
        denied = client.get("/api/templates", headers={"host": "192.168.1.20:8000"})
        self.assertEqual(denied.status_code, 403)

    def test_http_confirm_gate_and_export(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        base = read_json(ROOT / "fixtures/base-resume.sample.json")
        with patch.object(self.web, "store") as store:
            store.load.return_value = None
            self.assertIsNone(client.get("/api/resume").json())
            resume_response = client.put("/api/resume", json=base)
            self.assertEqual(resume_response.status_code, 200)
            store.save.assert_called_once_with(base)
        generated = read_json(ROOT / "fixtures/generated-resume.sample.json")
        result = client.post("/api/export", json={"template_id": "template-01", "format": "docx", "resume": generated})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.content.startswith(b"PK"))
        with patch.object(self.web.ai, "structure_resume", return_value=base):
            # Invalid/empty document fails before the AI step and cannot save.
            response = client.post("/api/import", files={"file": ("empty.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")})
        self.assertEqual(response.status_code, 422)
        self.assertIn(response.json()["error"]["code"], {"no_extractable_text", "invalid_document"})

    def test_export_uses_edited_plain_resume_text(self):
        from fastapi.testclient import TestClient
        from docx import Document
        from io import BytesIO
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        body = "虚构测试·林知夏\nlin.test@example.invalid · 000-0000-0000\n\n个人简介\n虚构测试：用户修改后的简历正文。\n\n工作经历\n虚构测试公司 · 数据助理\n• 虚构测试：完成资料整理。"
        response = client.post("/api/export", json={"template_id": "template-02", "format": "docx", "body_text": body})
        self.assertEqual(response.status_code, 200)
        text = "\n".join(p.text for p in Document(BytesIO(response.content)).paragraphs)
        self.assertIn("用户修改后的简历正文", text)
        self.assertIn("完成资料整理", text)
        self.assertNotIn("{{", text)
        self.assertEqual(client.post("/api/export", json={"template_id": "template-02", "format": "docx", "body_text": ""}).status_code, 422)

    def test_import_reports_missing_api_key_actionably_without_saving(self):
        from fastapi.testclient import TestClient
        from docx import Document
        from tempfile import TemporaryDirectory
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fictional.docx"
            doc = Document(); doc.add_paragraph("虚构测试简历内容"); doc.save(path)
            with patch.object(self.web.ai, "structure_resume", side_effect=self.web.ai.ModelConfigurationError("请设置 OPENAI_API_KEY 环境变量。")), patch.object(self.web.store, "save") as save:
                response = client.post("/api/import", files={"file": ("fictional.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        self.assertEqual(response.status_code, 502)
        self.assertIn("OPENAI_API_KEY", response.json()["error"]["message"])
        save.assert_not_called()

    def test_import_masks_provider_error_body(self):
        from fastapi.testclient import TestClient
        from docx import Document
        from tempfile import TemporaryDirectory
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fictional.docx"
            doc = Document(); doc.add_paragraph("虚构测试简历内容"); doc.save(path)
            with patch.object(self.web.ai, "structure_resume", side_effect=RuntimeError("provider detail sk-secret-fragment")):
                response = client.post("/api/import", files={"file": ("fictional.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("sk-secret-fragment", response.text)
        self.assertIn("OPENAI_API_KEY", response.json()["error"]["message"])

    def test_import_classifies_bad_request_without_leaking_provider_body(self):
        from fastapi.testclient import TestClient
        from docx import Document
        from tempfile import TemporaryDirectory
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fictional.docx"
            doc = Document(); doc.add_paragraph("虚构测试简历内容"); doc.save(path)
            class BadRequestError(Exception):
                status_code = 400
                code = "invalid_json_schema"
                param = "text.format.schema"
                type = "invalid_request_error"
                request_id = "req-fictional-test"
            with patch.object(self.web.ai, "structure_resume", side_effect=BadRequestError("schema detail including sensitive body")):
                response = client.post("/api/import", files={"file": ("fictional.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        self.assertIn("结构化输出参数", response.json()["error"]["message"])
        self.assertNotIn("sensitive body", response.text)

    def test_http_import_returns_incomplete_work_as_unconfirmed_draft(self):
        from fastapi.testclient import TestClient
        from docx import Document
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        import json
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fictional.docx"
            doc = Document(); doc.add_paragraph("虚构测试简历：测试公司，数据助理。"); doc.save(path)
            model_result = {"personal": {"name": "", "email": "", "phone": "", "location": ""}, "work": [{"employer": "测试公司", "title": "数据助理"}]}
            fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(model_result, ensure_ascii=False))))
            with patch.object(self.web.ai, "_client", return_value=(fake, "test-model")), patch.object(self.web.store, "save") as save:
                response = client.post("/api/import", files={"file": ("fictional.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["draft"]["work"][0]["achievements"], [])
        save.assert_not_called()
        self.web.drafts.pop(response.json()["draft_id"], None)

    def test_import_fills_contacts_and_limits_skills_to_section(self):
        from copy import deepcopy
        from docx import Document
        from fastapi.testclient import TestClient
        from tempfile import TemporaryDirectory
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        base = read_json(ROOT / "fixtures/base-resume.sample.json")
        model_draft = deepcopy(base)
        model_draft["skills"] = [
            {"id": f"skill-{i}", "name": name, "level": ""}
            for i, name in enumerate(("Python", "沟通能力", "Python", "SQL"), 1)
        ]
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fictional.docx"
            doc = Document()
            for line in ("姓名：李小明", "邮箱：li.test@example.com", "电话：13800138000", "技能/技术栈", "Python、SQL", "工作经历", "沟通能力"):
                doc.add_paragraph(line)
            doc.save(path)
            with patch.object(self.web.ai, "structure_resume", return_value=model_draft) as structure, patch.object(self.web.store, "save") as save:
                response = client.post("/api/import", files={"file": ("fictional.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        self.assertEqual(response.status_code, 200)
        draft = response.json()["draft"]
        self.assertEqual(draft["personal"]["name"], "李小明")
        self.assertEqual(draft["personal"]["email"], "li.test@example.com")
        self.assertEqual(draft["personal"]["phone"], "13800138000")
        self.assertEqual([item["name"] for item in draft["skills"]], ["Python", "SQL"])
        for contact in ("李小明", "li.test@example.com", "13800138000"):
            self.assertIn(contact, structure.call_args.args[0])
        save.assert_not_called()
        self.web.drafts.pop(response.json()["draft_id"], None)

    def test_import_uses_model_contact_when_local_pattern_misses(self):
        from copy import deepcopy
        from docx import Document
        from fastapi.testclient import TestClient
        from tempfile import TemporaryDirectory
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        model_draft = deepcopy(read_json(ROOT / "fixtures/base-resume.sample.json"))
        model_draft["personal"]["name"] = "虚构测试候选人"
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fictional.docx"
            doc = Document()
            doc.add_paragraph("虚构测试数据：候选人信息采用非标准格式")
            doc.save(path)
            with patch.object(self.web.ai, "structure_resume", return_value=model_draft):
                response = client.post("/api/import", files={"file": ("fictional.docx", path.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["draft"]["personal"]["name"], "虚构测试候选人")
        self.web.drafts.pop(response.json()["draft_id"], None)


if __name__ == "__main__":
    unittest.main()
