import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cvbuilder.job_storage import JobRecordStore


class JobRecordStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = JobRecordStore(Path(self.temp.name))
        self.content = {"summary": "虚构测试：项目协调经验", "fixture_note": "虚构测试数据"}
        self.order = ["summary", "work", "education"]
        self.visual = {"template_id": "template-01", "color": "#13766c", "page_width": "standard"}

    def create_job(self):
        company = self.store.add_company("虚构测试公司")
        job = self.store.add_job(company["id"], "虚构测试职位", "虚构测试招聘要求")
        return company, job

    def test_add_records_and_load_after_reopen(self):
        company, job = self.create_job()
        reopened = JobRecordStore(Path(self.temp.name))
        state = reopened.load()
        self.assertEqual(state["companies"][0]["id"], company["id"])
        self.assertEqual(state["companies"][0]["jobs"][0]["id"], job["id"])
        self.assertEqual(state["companies"][0]["jobs"][0]["job_text"], "虚构测试招聘要求")
        self.assertEqual(reopened.list(), state)

    def test_versions_increment_per_job_and_preserve_previous_versions(self):
        company, job = self.create_job()
        another_job = self.store.add_job(company["id"], "另一个虚构测试职位", "另一份虚构测试要求")
        first = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        second = self.store.create_version(company["id"], job["id"], {"summary": "虚构测试 V2"}, self.order, self.visual)
        independent = self.store.create_version(company["id"], another_job["id"], self.content, self.order, self.visual)
        self.assertEqual([first["number"], second["number"], independent["number"]], [1, 2, 1])
        state = self.store.load()
        versions = state["companies"][0]["jobs"][0]["versions"]
        self.assertEqual([version["id"] for version in versions], [first["id"], second["id"]])

    def test_update_overwrites_selected_version_without_allocating_number(self):
        company, job = self.create_job()
        first = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        updated = self.store.update_version(first["id"], {"summary": "虚构测试：编辑后的简介"}, ["work", "summary"], {"template_id": "template-02"})
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["number"], 1)
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertEqual(updated["resume_content"]["summary"], "虚构测试：编辑后的简介")
        self.assertEqual(updated["module_order"], ["work", "summary"])
        self.assertEqual(len(self.store.load()["companies"][0]["jobs"][0]["versions"]), 1)

    def test_failed_atomic_replace_keeps_previous_records(self):
        company = self.store.add_company("虚构测试公司")
        with patch("cvbuilder.job_storage.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                self.store.add_company("另一虚构测试公司")
        self.assertEqual([row["id"] for row in self.store.load()["companies"]], [company["id"]])

    def test_reset_removes_only_job_records(self):
        self.create_job()
        self.store.reset()
        self.assertEqual(self.store.load(), {"companies": []})
        self.assertFalse(self.store.path.exists())

    def test_unknown_parent_or_version_is_rejected(self):
        with self.assertRaises(KeyError):
            self.store.add_job("missing-company", "虚构测试职位", "虚构测试要求")
        with self.assertRaises(KeyError):
            self.store.update_version("missing-version", self.content, self.order, self.visual)


if __name__ == "__main__":
    unittest.main()
