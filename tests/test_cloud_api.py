"""Cloud API boundary tests using fictional IDs and records."""
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from cvbuilder import cloud


class CloudApiTests(unittest.TestCase):
    def test_s3_upload_urls_use_signature_v4(self):
        with patch("boto3.client") as create:
            cloud.aws_client("s3")
        self.assertEqual(create.call_args.kwargs["config"].signature_version, "s3v4")

    def request(self, claims=None):
        event = {"requestContext": {"authorizer": {"jwt": {"claims": claims or {}}}}}
        return Request({"type": "http", "aws.event": event, "method": "GET", "path": "/"})

    def test_identity_comes_only_from_verified_gateway_claims(self):
        with patch.dict(os.environ, {"CV_COGNITO_ISSUER": "https://fictional-issuer", "CV_COGNITO_CLIENT_ID": "fictional-client"}):
            with self.assertRaises(HTTPException) as missing:
                cloud.user_id(self.request())
            self.assertEqual(missing.exception.status_code, 401)
            claims = {"sub": "fictional-user-a", "token_use": "access", "iss": "https://fictional-issuer", "client_id": "fictional-client"}
            self.assertEqual(cloud.user_id(self.request(claims)), "fictional-user-a")
            for bad in ({**claims, "token_use": "id"}, {**claims, "iss": "https://other"}, {**claims, "client_id": "other"}):
                with self.assertRaises(HTTPException):
                    cloud.user_id(self.request(bad))

    def test_request_without_gateway_identity_is_rejected(self):
        from fastapi.testclient import TestClient
        response = TestClient(cloud.app).get("/api/resume")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthorized")
        self.assertEqual(TestClient(cloud.app).options("/api/resume").status_code, 204)

    def test_export_requires_version_in_scoped_store(self):
        class Store:
            user_id = "fictional-user-a"
            def get_job(self, job_id):
                if job_id != "owned-job":
                    raise KeyError(job_id)
                return ({"id": "owned-company"}, {"versions": [{"id": "owned-version"}]})
        store = Store()
        payload = {"job_id": "other-job", "version_id": "owned-version", "template_id": "template-01", "format": "pdf"}
        with self.assertRaises(HTTPException) as denied:
            cloud.export(payload, store)
        self.assertEqual(denied.exception.status_code, 404)
        payload["job_id"] = "owned-job"
        payload["version_id"] = "other-version"
        with self.assertRaises(HTTPException):
            cloud.export(payload, store)

    def test_existing_v1_blocks_another_ai_request(self):
        class Store:
            def get_job(self, job_id):
                return ({"id": "fictional-company"}, {"versions": [{"id": "fictional-v1"}]})
        with self.assertRaises(HTTPException) as blocked:
            cloud.create_version("fictional-job", Store())
        self.assertEqual(blocked.exception.status_code, 409)
        self.assertEqual(blocked.exception.detail["code"], "version_exists")

    def test_edit_saves_new_version_through_store(self):
        class Store:
            def save_edited_version(self, job_id, source_id, content, order, settings):
                self.args = (job_id, source_id, content, order, settings)
                return {"id": "fictional-v2", "number": 2, "source_version_id": source_id}
        store = Store()
        payload = {"resume_content": {"summary": []}, "module_order": ["summary"], "visual_settings": {"template_id": "template-01"}}
        with patch("cvbuilder.cloud.validate_editor_payload"):
            result = cloud.update_version("fictional-job", "fictional-v1", payload, store)
        self.assertEqual(result["number"], 2)
        self.assertEqual(store.args[:2], ("fictional-job", "fictional-v1"))

    def test_rename_and_delete_routes_use_scoped_store(self):
        class Store:
            def __init__(self):
                self.calls = []
            def rename_company(self, *args):
                self.calls.append(("rename_company", *args))
                return {"id": args[0], "name": args[1]}
            def delete_company(self, *args):
                self.calls.append(("delete_company", *args))
            def rename_job(self, *args):
                self.calls.append(("rename_job", *args))
                return {"id": args[0], "title": args[1]}
            def delete_job(self, *args):
                self.calls.append(("delete_job", *args))
            def delete_version(self, *args):
                self.calls.append(("delete_version", *args))
        store = Store()
        cloud.rename_company("fictional-company", {"name": "虚构新公司"}, store)
        cloud.rename_job("fictional-job", {"title": "虚构新岗位"}, store)
        cloud.delete_version("fictional-job", "fictional-v1", store)
        cloud.delete_job("fictional-job", store)
        cloud.delete_company("fictional-company", store)
        self.assertEqual(store.calls, [
            ("rename_company", "fictional-company", "虚构新公司"),
            ("rename_job", "fictional-job", "虚构新岗位"),
            ("delete_version", "fictional-job", "fictional-v1"),
            ("delete_job", "fictional-job"),
            ("delete_company", "fictional-company"),
        ])

    def test_import_key_and_download_are_scoped(self):
        class Store:
            user_id = "fictional-user-a"
            def get_task(self, task_id):
                return {"kind": "export", "status": "succeeded", "result": {"key": "exports/fictional-user-b/" + task_id + ".pdf", "format": "pdf"}}
        store = Store()
        with self.assertRaises(HTTPException) as denied:
            cloud.import_resume({"key": "uploads/fictional-user-b/" + "a" * 32 + ".pdf"}, store)
        self.assertEqual(denied.exception.status_code, 422)
        with self.assertRaises(HTTPException) as denied:
            cloud.download("fictional-task", store)
        self.assertEqual(denied.exception.status_code, 404)

    def test_phase5_snapshot_queue_and_upload_scope(self):
        class Store:
            user_id = "fictional-user-a"
            def get_job(self, job_id):
                return ({"id": "fictional-company"}, {"id": job_id, "job_text": "虚构测试快照", "versions": [], "has_generated": False})
            def load_resume(self):
                return {"personal": {"name": "虚构测试用户"}}
        store = Store()
        with patch("cvbuilder.cloud._task", return_value={"task_id": "fictional-task", "status": "queued"}) as queue:
            self.assertEqual(cloud.create_version("fictional-job", store)["task_id"], "fictional-task")
        queue.assert_called_once_with(store, "generate_version", {"job_id": "fictional-job", "job_text": "虚构测试快照"})
        with self.assertRaises(HTTPException) as denied:
            cloud.extract_job({"key": "uploads/fictional-user-b/" + "a" * 32 + ".docx"}, store)
        self.assertEqual(denied.exception.status_code, 422)

    def test_phase5_answer_is_persisted_before_worker_dispatch(self):
        class Store:
            user_id = "fictional-user-a"
            def __init__(self):
                self.session = {"id": "fictional-session", "status": "awaiting_answer", "turns": [{"id": "fictional-turn", "question": "虚构测试问题", "answer": None}]}
            def get_interview(self, session_id):
                return self.session
            def update_interview(self, session_id, patch, expected_status=None):
                self.session = {**self.session, **patch}
                return self.session
        store = Store()
        def dispatch(*args):
            self.assertEqual(store.session["turns"][0]["answer"], "虚构测试回答")
            return {"task_id": "fictional-task", "status": "queued"}
        with patch("cvbuilder.cloud._task", side_effect=dispatch):
            result = cloud.answer_interview("fictional-session", {"answer": "虚构测试回答"}, store)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(store.session["status"], "feedback_pending")

    def test_reference_can_be_queued_before_answer(self):
        class Store:
            user_id = "fictional-user-a"
            def __init__(self):
                self.session = {"status": "awaiting_answer", "turns": [{"id": "fictional-turn", "question": "虚构测试问题", "answer": None}]}
            def get_interview(self, session_id):
                return self.session
            def update_interview(self, session_id, patch, expected_status=None):
                self.session = {**self.session, **patch}
                return self.session
        store = Store()
        with patch("cvbuilder.cloud._task", return_value={"task_id": "fictional-task"}) as queue:
            result = cloud.reference_interview("fictional-session", store)
        self.assertEqual(result["task_id"], "fictional-task")
        self.assertEqual(store.session["status"], "reference_pending")
        queue.assert_called_once_with(store, "interview_reference", {"session_id": "fictional-session", "turn_id": "fictional-turn"})
        store.session["status"] = "awaiting_answer"
        with patch("cvbuilder.cloud._task", side_effect=HTTPException(503, "fictional dispatch failure")):
            with self.assertRaises(HTTPException):
                cloud.reference_interview("fictional-session", store)
        self.assertEqual(store.session["status"], "awaiting_answer")

    def test_generic_reference_is_upgraded_from_confirmed_resume(self):
        class Store:
            def __init__(self):
                self.session = {"status": "awaiting_answer", "turns": [{
                    "id": "fictional-turn", "category": "简历经历", "question": "请介绍虚构测试项目",
                    "answer": None, "reference_answer": "参考答题框架（请按真实经历填写）：[真实经历]"}]}
            def get_interview(self, session_id):
                return self.session
            def load_resume(self):
                return {"projects": [{"name": "虚构测试项目", "role": "开发成员", "description": "维护测试用例。"}]}
            def update_interview(self, session_id, patch, expected_status=None):
                self.session = {**self.session, **patch}
                return self.session
        store = Store()
        result = cloud.reference_interview("fictional-session", store)
        self.assertEqual(result["status"], "ready")
        self.assertIn("虚构测试项目", store.session["turns"][0]["reference_answer"])
        self.assertNotIn("参考答题框架", store.session["turns"][0]["reference_answer"])


if __name__ == "__main__":
    unittest.main()
