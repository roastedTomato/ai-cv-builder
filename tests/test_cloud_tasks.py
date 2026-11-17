"""Cloud worker tests use only fictional test data and in-memory AWS fakes."""
from __future__ import annotations

import io
import os
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from docx import Document

from cvbuilder.cli import ROOT, read_json
from cvbuilder.cloud_tasks import MAX_INLINE_RESULT_BYTES, MAX_UPLOAD_BYTES, TaskFailure, _load_openai_key, process_task
from cvbuilder.templates import find_soffice


class FakeStore:
    def __init__(self, user="fictional-user-a"):
        self.user = user
        self.tasks = {}
        self.resume = read_json(ROOT / "fixtures/base-resume.sample.json")
        self.job = {"id": "fictional-job", "job_text": "虚构测试岗位", "versions": [], "has_generated": False}
        self.company = {"id": "fictional-company"}

    def get_task(self, task_id):
        task = self.tasks.get(task_id)
        return deepcopy(task) if task else None

    def update_task(self, task_id, patch):
        self.tasks[task_id].update(deepcopy(patch))
        return self.get_task(task_id)

    def load_resume(self):
        return self.resume

    def get_job(self, job_id):
        if job_id != self.job["id"]:
            raise KeyError(job_id)
        return self.company, self.job

    def create_version(self, company_id, job_id, content, order, settings, job_text=None, *, initial_only=False):
        if initial_only and (self.job["versions"] or self.job["has_generated"]):
            raise ValueError("岗位已有简历版本")
        version = {"id": f"fictional-version-{len(self.job['versions']) + 1}", "number": len(self.job["versions"]) + 1,
                   "job_text": job_text, "resume_content": content, "module_order": order, "visual_settings": settings}
        self.job["versions"].append(version)
        self.job["has_generated"] = True
        return version

    def queue(self, kind, params, task_id="fictional-task"):
        self.tasks[task_id] = {"id": task_id, "kind": kind, "status": "queued", "input": params, "result": None, "error": None}
        return task_id


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def get_object(self, Bucket, Key):
        data = self.objects[(Bucket, Key)]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def delete_object(self, Bucket, Key):
        self.deleted.append((Bucket, Key))
        self.objects.pop((Bucket, Key), None)

    def upload_file(self, Filename, Bucket, Key, ExtraArgs):
        self.objects[(Bucket, Key)] = Path(Filename).read_bytes()

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = Body


class CloudTaskTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.s3 = FakeS3()
        self.bucket = "fictional-test-bucket"

    def run_task(self, task_id="fictional-task", user="fictional-user-a"):
        return process_task(user, task_id, store=self.store, s3_client=self.s3, bucket=self.bucket)

    def test_generate_version_creates_only_v1_from_confirmed_resume(self):
        generated = read_json(ROOT / "fixtures/generated-resume.sample.json")
        self.store.queue("generate_version", {"job_id": "fictional-job"})
        with patch("cvbuilder.cloud_tasks.ai.generate_resume_from_job_unchecked", return_value=generated) as ai_call:
            result = self.run_task()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["version"]["number"], 1)
        ai_call.assert_called_once_with(self.store.resume, "虚构测试岗位")
        self.assertEqual(self.run_task(), result)
        self.store.queue("generate_version", {"job_id": "fictional-job"}, "fictional-task-2")
        with patch("cvbuilder.cloud_tasks.ai.generate_resume_from_job_unchecked", return_value=generated) as second_ai:
            second = self.run_task("fictional-task-2")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["error"]["code"], "version_exists")
        self.assertIn("已有简历版本", second["error"]["message"])
        second_ai.assert_not_called()
        self.assertEqual(len(self.store.job["versions"]), 1)

    def test_generate_version_is_blocked_after_all_versions_deleted(self):
        self.store.job["has_generated"] = True
        self.store.job["versions"] = []
        self.store.queue("generate_version", {"job_id": "fictional-job"})
        with patch("cvbuilder.cloud_tasks.ai.generate_resume_from_job_unchecked") as ai_call:
            result = self.run_task()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "version_exists")
        ai_call.assert_not_called()

    def test_concurrent_generate_cannot_create_v2(self):
        generated = read_json(ROOT / "fixtures/generated-resume.sample.json")
        self.store.queue("generate_version", {"job_id": "fictional-job"})
        original_create = self.store.create_version

        def simultaneous_v1(*args, **kwargs):
            original_create(*args, **kwargs)
            raise ValueError("岗位已有简历版本")

        with patch("cvbuilder.cloud_tasks.ai.generate_resume_from_job_unchecked", return_value=generated), \
             patch.object(self.store, "create_version", side_effect=simultaneous_v1):
            result = self.run_task()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "version_exists")
        self.assertEqual(len(self.store.job["versions"]), 1)

    def test_duplicate_worker_does_not_generate_another_version(self):
        self.store.queue("generate_version", {"job_id": "fictional-job"})
        original_update = self.store.update_task

        def claimed_by_another_worker(task_id, patch):
            if patch.get("status") == "running":
                original_update(task_id, patch)
                raise ValueError("任务已被领取")
            return original_update(task_id, patch)

        with patch.object(self.store, "update_task", side_effect=claimed_by_another_worker), \
             patch("cvbuilder.cloud_tasks.ai.generate_resume_from_job_unchecked") as ai_call:
            result = self.run_task()
        self.assertEqual(result["status"], "running")
        ai_call.assert_not_called()
        self.assertEqual(self.store.job["versions"], [])

    def test_provider_failure_masks_sensitive_details(self):
        self.store.queue("generate", {"job_text": "虚构测试职位"})
        with patch("cvbuilder.cloud_tasks.ai.generate_resume_from_job_unchecked", side_effect=RuntimeError("secret-sensitive-document")):
            result = self.run_task()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "task_failed")
        self.assertNotIn("secret-sensitive-document", str(result))

    def test_openai_secret_is_loaded_and_cached_without_exposing_value(self):
        import cvbuilder.cloud_tasks as worker
        class Secrets:
            calls = 0
            def get_secret_value(self, SecretId):
                self.calls += 1
                self.last_id = SecretId
                return {"SecretString": '{"OPENAI_API_KEY":"fictional-test-secret"}'}
        fake = Secrets()
        previous = worker._cached_secret_arn
        worker._cached_secret_arn = None
        try:
            with patch.dict(os.environ, {"CV_OPENAI_SECRET_ARN": "fictional-secret-arn"}):
                with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                    _load_openai_key(fake)
                    _load_openai_key(fake)
                    self.assertEqual(fake.calls, 1)
                    self.assertEqual(os.environ["OPENAI_API_KEY"], "fictional-test-secret")
        finally:
            worker._cached_secret_arn = previous

    def test_missing_secret_fails_without_leaking_provider_detail(self):
        import cvbuilder.cloud_tasks as worker
        class Secrets:
            def get_secret_value(self, SecretId):
                raise RuntimeError("sensitive-secret-provider-detail")
        previous = worker._cached_secret_arn
        worker._cached_secret_arn = None
        try:
            with patch.dict(os.environ, {"CV_OPENAI_SECRET_ARN": "fictional-missing-arn"}):
                with self.assertRaises(TaskFailure) as caught:
                    _load_openai_key(Secrets())
            self.assertEqual(caught.exception.code, "ai_configuration")
            self.assertNotIn("sensitive-secret-provider-detail", caught.exception.message)
        finally:
            worker._cached_secret_arn = previous

    def test_large_result_uses_expiring_s3_object(self):
        self.store.queue("generate", {"job_text": "虚构测试职位"})
        fictional_output = {"summary": [{"text": "虚构测试数据" * (MAX_INLINE_RESULT_BYTES // 10)}]}
        with patch("cvbuilder.cloud_tasks.ai.generate_resume_from_job_unchecked", return_value=fictional_output):
            result = self.run_task()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"], {"result_key": "results/fictional-user-a/fictional-task.json"})
        self.assertIn(b"summary", self.s3.objects[(self.bucket, result["result"]["result_key"])])

    def test_import_file_size_and_temporary_cleanup(self):
        self.store.queue("import", {"suffix": ".pdf"})
        key = (self.bucket, "uploads/fictional-user-a/fictional-task.pdf")
        self.s3.objects[key] = b"x" * (MAX_UPLOAD_BYTES + 1)
        result = self.run_task()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "file_too_large")
        self.assertIn(key, self.s3.deleted)

    def test_import_is_unconfirmed_draft_and_uses_fictional_document(self):
        upload_key = "uploads/fictional-user-a/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.docx"
        self.store.queue("import", {"key": upload_key})
        document = Document()
        document.add_paragraph("虚构测试数据：李小明，数据助理。")
        stream = io.BytesIO()
        document.save(stream)
        self.s3.objects[(self.bucket, upload_key)] = stream.getvalue()
        draft = read_json(ROOT / "fixtures/base-resume.sample.json")
        with patch("cvbuilder.cloud_tasks.ai.structure_resume", return_value=draft):
            result = self.run_task()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["draft_id"], "fictional-task")
        self.assertEqual(result["result"]["draft"]["resume_id"], draft["resume_id"])
        self.assertEqual(self.store.resume, read_json(ROOT / "fixtures/base-resume.sample.json"))
        self.assertNotIn((self.bucket, upload_key), self.s3.objects)

    def test_import_rejects_another_users_upload_key(self):
        self.store.queue("import", {"key": "uploads/fictional-user-b/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.docx"})
        result = self.run_task()
        self.assertEqual(result["error"]["code"], "invalid_task")
        self.assertEqual(self.s3.deleted, [])

    def test_export_requires_owned_version_and_removes_placeholders(self):
        generated = read_json(ROOT / "fixtures/generated-resume.sample.json")
        self.store.create_version("fictional-company", "fictional-job", generated, ["work", "summary"],
                                  {"template_id": "template-01", "color": "#2C5263", "page_width": "standard"})
        self.store.queue("export", {"job_id": "someone-elses-job", "version_id": "fictional-version-1", "template_id": "template-01", "format": "docx"})
        denied = self.run_task()
        self.assertEqual(denied["error"]["code"], "not_found")
        for index in range(1, 4):
            task_id = f"fictional-export-{index}"
            self.store.queue("export", {"job_id": "fictional-job", "version_id": "fictional-version-1", "template_id": f"template-{index:02d}", "format": "docx"}, task_id)
            result = self.run_task(task_id)
            self.assertEqual(result["status"], "succeeded")
            data = self.s3.objects[(self.bucket, result["result"]["key"])]
            rendered = Document(io.BytesIO(data))
            text = "\n".join(p.text for p in rendered.paragraphs)
            self.assertNotIn("{{", text)
            self.assertIn(generated["personal"]["name"], text)
            self.assertIn("个人简介", text)
            self.assertNotIn("SUMMARY", text)

    @unittest.skipUnless(find_soffice(), "LibreOffice is unavailable")
    def test_pdf_export_renders_fictional_chinese_content(self):
        from pypdf import PdfReader
        generated = read_json(ROOT / "fixtures/generated-resume.sample.json")
        self.store.create_version("fictional-company", "fictional-job", generated, ["summary", "work"],
                                  {"template_id": "template-01", "color": "#2C5263", "page_width": "standard"})
        self.store.queue("export", {"job_id": "fictional-job", "version_id": "fictional-version-1", "template_id": "template-01", "format": "pdf"})
        result = self.run_task()
        self.assertEqual(result["status"], "succeeded")
        data = self.s3.objects[(self.bucket, result["result"]["key"])]
        self.assertTrue(data.startswith(b"%PDF"))
        with TemporaryDirectory() as folder:
            path = Path(folder) / "fictional.pdf"
            path.write_bytes(data)
            text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        self.assertIn(generated["personal"]["name"], text)
        self.assertNotIn("{{", text)


if __name__ == "__main__":
    unittest.main()
