"""Local API integration against an isolated MySQL database with fictional data."""
import os
import unittest
import uuid
from unittest.mock import patch

from cvbuilder.cli import ROOT, read_json


@unittest.skipUnless(os.environ.get("CV_MYSQL_TEST_DATABASE"), "set CV_MYSQL_TEST_DATABASE for MySQL API tests")
class MySQLWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cvbuilder import web
        cls.web = web
        cls.original_database = os.environ.get("CV_MYSQL_DATABASE")
        os.environ["CV_MYSQL_DATABASE"] = os.environ["CV_MYSQL_TEST_DATABASE"]

    @classmethod
    def tearDownClass(cls):
        if cls.original_database is None:
            os.environ.pop("CV_MYSQL_DATABASE", None)
        else:
            os.environ["CV_MYSQL_DATABASE"] = cls.original_database

    def test_resume_job_versions_and_interview_survive_reconnect(self):
        from fastapi.testclient import TestClient
        from cvbuilder import interview_ai
        from cvbuilder.mysql_storage import MySQLJobStore, MySQLResumeStore

        user_id = "fictional-web-" + uuid.uuid4().hex
        resume_store = MySQLResumeStore(user_id)
        job_store = MySQLJobStore(user_id)
        self.addCleanup(resume_store.reset)
        self.addCleanup(job_store.reset)
        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        base = read_json(ROOT / "fixtures/base-resume.sample.json")
        draft = {"summary": [{"text": "虚构测试岗位简介"}]}
        with patch.object(self.web, "store", resume_store), patch.object(self.web, "job_store", job_store), \
             patch.object(self.web.ai, "generate_resume_from_job_unchecked", return_value=draft), \
             patch.object(interview_ai, "generate_questions", return_value=[{
                 "question": f"虚构测试：请介绍经历 {i}", "category": "简历经历",
                 "reference_answer": "虚构测试：据实回答"} for i in range(1, 13)]):
            self.assertEqual(client.put("/api/resume", json=base).status_code, 200)
            company = client.post("/api/companies", json={"name": "虚构测试公司"}).json()
            job = client.post(f"/api/companies/{company['id']}/jobs", json={
                "title": "虚构测试职位", "job_text": "虚构测试旧要求"}).json()
            first = client.post(f"/api/jobs/{job['id']}/versions").json()
            self.assertEqual(first["number"], 1)
            self.assertEqual(client.post(f"/api/jobs/{job['id']}/versions").status_code, 409)
            changed = client.patch(f"/api/jobs/{job['id']}", json={
                "title": "虚构测试职位", "job_text": "虚构测试新要求", "expected_revision": 0})
            self.assertEqual(changed.status_code, 200)
            edited = client.post(f"/api/jobs/{job['id']}/versions/{first['id']}/edits", json={
                "resume_content": draft, "module_order": ["summary"],
                "visual_settings": {"template_id": "template-01", "color": "#2C5263", "page_width": "standard"}})
            self.assertEqual(edited.status_code, 200)
            self.assertEqual(edited.json()["job_text"], "虚构测试新要求")
            session = client.post(f"/api/jobs/{job['id']}/versions/{first['id']}/interviews").json()
            self.assertEqual(session["job_text"], "虚构测试旧要求")
            self.assertEqual(session["turns"][0]["question"], "虚构测试：请介绍经历 1")
            self.assertEqual(MySQLResumeStore(user_id).load(), base)
            reopened = MySQLJobStore(user_id)
            self.assertEqual([v["number"] for v in reopened.load()["companies"][0]["jobs"][0]["versions"]], [1, 2])
            self.assertEqual(reopened.get_interview(session["id"])["job_text"], "虚构测试旧要求")
            self.assertEqual(client.post("/api/reset").status_code, 200)
            self.assertIsNone(MySQLResumeStore(user_id).load())
            self.assertEqual(MySQLJobStore(user_id).load(), {"companies": []})

    def test_generation_rejects_changed_job_requirement(self):
        from fastapi.testclient import TestClient
        from cvbuilder.mysql_storage import MySQLJobStore, MySQLResumeStore

        user_id = "fictional-race-" + uuid.uuid4().hex
        resume_store = MySQLResumeStore(user_id)
        job_store = MySQLJobStore(user_id)
        self.addCleanup(resume_store.reset)
        self.addCleanup(job_store.reset)
        resume_store.save(read_json(ROOT / "fixtures/base-resume.sample.json"))
        company = job_store.add_company("虚构测试公司")
        job = job_store.add_job(company["id"], "虚构测试岗位", "虚构测试旧要求")

        def change_requirement(_resume, _job_text):
            job_store.update_job(job["id"], "虚构测试岗位", "虚构测试新要求", 0)
            return {"summary": [{"text": "虚构测试旧要求生成内容"}]}

        client = TestClient(self.web.app, base_url="http://127.0.0.1")
        with patch.object(self.web, "store", resume_store), patch.object(self.web, "job_store", job_store), \
             patch.object(self.web.ai, "generate_resume_from_job_unchecked", side_effect=change_requirement):
            result = client.post(f"/api/jobs/{job['id']}/versions")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["error"]["code"], "conflict")
        self.assertEqual(job_store.get_job(job["id"])[1]["version_counter"], 0)


if __name__ == "__main__":
    unittest.main()
