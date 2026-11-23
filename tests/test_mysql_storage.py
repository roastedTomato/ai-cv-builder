"""Integration tests for an isolated MySQL test database; all records are fictional."""
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from cvbuilder.mysql_storage import MySQLJobStore, MySQLResumeStore, MySQLStore


@unittest.skipUnless(os.environ.get("CV_MYSQL_TEST_DATABASE"), "set CV_MYSQL_TEST_DATABASE for MySQL integration tests")
class MySQLStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_database = os.environ.get("CV_MYSQL_DATABASE")
        os.environ["CV_MYSQL_DATABASE"] = os.environ["CV_MYSQL_TEST_DATABASE"]

    @classmethod
    def tearDownClass(cls):
        if cls.original_database is None:
            os.environ.pop("CV_MYSQL_DATABASE", None)
        else:
            os.environ["CV_MYSQL_DATABASE"] = cls.original_database

    def setUp(self):
        self.user = "fictional-test-" + uuid.uuid4().hex
        self.other = "fictional-test-" + uuid.uuid4().hex
        self.store = MySQLStore(self.user)
        self.addCleanup(self.store.reset)
        self.content = {"summary": ["虚构测试：项目协调经验"]}
        self.order = ["summary"]
        self.settings = {"template_id": "template-01", "color": "#13766c", "page_width": "standard"}

    def _job(self):
        company = self.store.add_company("虚构测试公司")
        job = self.store.add_job(company["id"], "虚构测试职位", "虚构测试招聘要求")
        return company, job

    def test_resume_and_record_user_isolation_and_reconnect(self):
        other = MySQLStore(self.other)
        self.addCleanup(other.reset)
        self.store.save_resume({"name": "虚构测试用户甲"})
        other.save_resume({"name": "虚构测试用户乙"})
        company, job = self._job()
        self.assertEqual(MySQLStore(self.user).load_resume(), {"name": "虚构测试用户甲"})
        self.assertEqual(other.load_resume(), {"name": "虚构测试用户乙"})
        self.assertEqual(other.load()["companies"], [])
        with self.assertRaises(KeyError):
            other.get_job(job["id"])
        with self.assertRaises(KeyError):
            other.add_job(company["id"], "虚构测试岗位", "虚构测试要求")
        self.store.reset()
        self.assertEqual(other.load_resume(), {"name": "虚构测试用户乙"})

    def test_version_counter_snapshot_and_source_survive_delete(self):
        company, job = self._job()
        first = self.store.create_version(
            company["id"], job["id"], self.content, self.order, self.settings,
            job_text="虚构测试招聘要求"
        )
        with self.assertRaisesRegex(ValueError, "V1"):
            self.store.create_version(company["id"], job["id"], self.content, self.order, self.settings)
        changed = self.store.update_job(job["id"], "虚构新职位", "虚构新要求", 0)
        self.assertEqual(changed["revision"], 1)
        second = self.store.save_edited_version(job["id"], first["id"], self.content, self.order, self.settings)
        self.assertEqual(second["number"], 2)
        self.assertEqual(second["job_text"], "虚构新要求")
        self.assertEqual(second["source_version_id"], first["id"])
        self.store.delete_version(job["id"], first["id"])
        saved = MySQLStore(self.user).get_job(job["id"])[1]
        self.assertEqual(saved["versions"][0]["source_version_id"], first["id"])
        self.assertEqual(saved["versions"][0]["source_version_number"], 1)
        self.store.delete_version(job["id"], second["id"])
        self.assertEqual(self.store.get_job(job["id"])[1]["version_counter"], 2)
        with self.assertRaisesRegex(ValueError, "V1"):
            self.store.create_version(company["id"], job["id"], self.content, self.order, self.settings)

    def test_concurrent_initial_version_and_edits(self):
        company, job = self._job()
        def first(_):
            try:
                return self.store.create_version(company["id"], job["id"], self.content, self.order, self.settings)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            initial = [value for value in pool.map(first, range(4)) if value]
        self.assertEqual(len(initial), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            edited = list(pool.map(
                lambda _: self.store.save_edited_version(job["id"], initial[0]["id"],
                                                          self.content, self.order, self.settings),
                range(8)
            ))
        self.assertEqual(sorted(v["number"] for v in edited), list(range(2, 10)))

    def test_conflict_and_failed_write_leave_original(self):
        company, job = self._job()
        self.assertEqual(self.store.rename_company(company["id"], "虚构测试公司")["name"], "虚构测试公司")
        with self.assertRaisesRegex(ValueError, "修改"):
            self.store.update_job(job["id"], "虚构新职位", "虚构新要求", 5)
        self.assertEqual(self.store.get_job(job["id"])[1]["job_text"], "虚构测试招聘要求")
        with self.assertRaisesRegex(ValueError, "修改"):
            self.store.create_version(company["id"], job["id"], self.content, self.order,
                                      self.settings, job_text="不匹配的虚构要求")
        self.assertEqual(self.store.get_job(job["id"])[1]["version_counter"], 0)

    def test_interview_snapshot_turns_and_cascade(self):
        company, job = self._job()
        first = self.store.create_version(company["id"], job["id"], self.content, self.order, self.settings)
        session = self.store.create_interview(job["id"], first["id"])
        turn = {"id": uuid.uuid4().hex, "question": "虚构测试问题", "category": "resume",
                "reference_answer": "虚构测试回答", "answer": None, "feedback": None,
                "follow_up": None, "created_at": session["created_at"]}
        updated = self.store.update_interview(session["id"], {"status": "awaiting_answer", "turns": [turn]}, "new")
        self.assertEqual(updated["turns"], [turn])
        with self.assertRaisesRegex(ValueError, "状态已更新"):
            self.store.update_interview(session["id"], {"status": "failed"}, "new")
        with self.assertRaisesRegex(ValueError, "12"):
            self.store.update_interview(session["id"], {"turns": [turn] * 13})
        self.store.delete_version(job["id"], first["id"])
        self.assertTrue(MySQLStore(self.user).get_interview(session["id"])["version_deleted"])
        self.assertEqual(self.store.get_interview(session["id"])["job_text"], "虚构测试招聘要求")
        self.store.delete_company(company["id"])
        self.assertIsNone(self.store.get_interview(session["id"]))

    def test_latest_interview_and_task_claim(self):
        company, job = self._job()
        version = self.store.create_version(company["id"], job["id"], self.content, self.order, self.settings)
        old = self.store.create_interview(job["id"], version["id"])
        new = self.store.create_interview(job["id"], version["id"])
        self.assertIsNone(self.store.get_interview(old["id"]))
        self.assertEqual([s["id"] for s in self.store.list_interviews(job["id"])], [new["id"]])
        task = {"id": uuid.uuid4().hex, "kind": "generate", "status": "queued",
                "input": {"job_text": "虚构测试要求"}}
        self.store.create_task(task)
        self.assertEqual(self.store.update_task(task["id"], {"status": "running"})["status"], "running")
        with self.assertRaisesRegex(ValueError, "已被领取"):
            self.store.update_task(task["id"], {"status": "running"})
        self.assertIsNone(MySQLStore(self.other).get_task(task["id"]))

    def test_answer_time_survives_turn_rewrite_and_reconnect(self):
        company, job = self._job()
        version = self.store.create_version(company["id"], job["id"], self.content, self.order, self.settings)
        session = self.store.create_interview(job["id"], version["id"])
        answered_at = "2026-01-02T03:04:05+00:00"  # fictional test timestamp
        turn = {"id": uuid.uuid4().hex, "question": "虚构测试问题", "category": "自我介绍",
                "reference_answer": "虚构测试参考答案", "answer": "虚构测试回答",
                "answered_at": answered_at, "feedback": None, "follow_up": None,
                "created_at": session["created_at"]}
        self.store.update_interview(session["id"], {"status": "feedback_pending", "turns": [turn]}, "new")
        reopened = MySQLStore(self.user)
        self.assertEqual(reopened.get_interview(session["id"])["turns"][0]["answered_at"], answered_at)
        reopened.update_interview(session["id"], {"status": "feedback_ready", "turns": [turn]}, "feedback_pending")
        self.assertEqual(MySQLStore(self.user).get_interview(session["id"])["turns"][0]["answered_at"], answered_at)

    def test_local_adapters_have_independent_reset(self):
        resume = MySQLResumeStore(self.user)
        jobs = MySQLJobStore(self.user)
        resume.save({"name": "虚构测试用户"})
        jobs.add_company("虚构测试公司")
        jobs.reset()
        self.assertEqual(jobs.load()["companies"], [])
        self.assertEqual(resume.load(), {"name": "虚构测试用户"})
        resume.reset()
        self.assertIsNone(resume.load())

    def test_cloud_credentials_are_loaded_from_secret(self):
        from cvbuilder import mysql_storage
        from types import SimpleNamespace
        fake = SimpleNamespace(get_secret_value=lambda **_: {
            "SecretString": '{"username":"fictional_user","password":"fictional_password"}'})
        with patch.dict(os.environ, {"CV_MYSQL_SECRET_ARN": "fictional-secret-arn"}), \
             patch("boto3.client", return_value=fake):
            mysql_storage._cached_db_secret = None
            self.assertEqual(mysql_storage._database_credentials(),
                             ("fictional_user", "fictional_password"))
        mysql_storage._cached_db_secret = None


if __name__ == "__main__":
    unittest.main()
