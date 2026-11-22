"""Cloud entrypoints use the shared MySQL store with fictional test records."""
import os
import unittest
import uuid
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("CV_MYSQL_TEST_DATABASE"), "set CV_MYSQL_TEST_DATABASE for cloud MySQL tests")
class CloudMySQLTests(unittest.TestCase):
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

    def test_worker_claims_task_and_persists_version_in_mysql(self):
        from cvbuilder import cloud, cloud_tasks
        from cvbuilder.mysql_storage import MySQLStore

        user_id = "fictional-cloud-" + uuid.uuid4().hex
        store = cloud.cloud_store(user_id)
        self.assertIsInstance(store, MySQLStore)
        self.addCleanup(store.reset)
        store.save_resume({"note": "虚构测试基础简历"})
        company = store.add_company("虚构测试公司")
        job = store.add_job(company["id"], "虚构测试职位", "虚构测试招聘要求")
        task_id = uuid.uuid4().hex
        store.create_task({"id": task_id, "kind": "generate_version", "status": "queued",
                           "input": {"job_id": job["id"], "job_text": job["job_text"]}})
        draft = {"summary": [{"text": "虚构测试岗位简介"}]}
        with patch.object(cloud_tasks, "_load_openai_key"), \
             patch.object(cloud_tasks.ai, "generate_resume_from_job_unchecked", return_value=draft):
            result = cloud_tasks.process_task(user_id, task_id, s3_client=object(), bucket="fictional-bucket")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(MySQLStore(user_id).get_job(job["id"])[1]["versions"][0]["resume_content"], draft)
        self.assertIsNone(MySQLStore("fictional-other-user").get_task(task_id))


if __name__ == "__main__":
    unittest.main()
