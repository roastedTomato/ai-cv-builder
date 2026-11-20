import tempfile
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
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
        second = self.store.save_edited_version(job["id"], first["id"], {"summary": "虚构测试 V2"}, self.order, self.visual)
        independent = self.store.create_version(company["id"], another_job["id"], self.content, self.order, self.visual)
        self.assertEqual([first["number"], second["number"], independent["number"]], [1, 2, 1])
        state = self.store.load()
        versions = state["companies"][0]["jobs"][0]["versions"]
        self.assertEqual([version["id"] for version in versions], [first["id"], second["id"]])
        self.assertEqual(versions[1]["source_version_id"], first["id"])
        with self.assertRaisesRegex(ValueError, "V1"):
            self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)

    def test_edit_creates_next_version_and_preserves_selected_version(self):
        company, job = self.create_job()
        first = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        updated = self.store.save_edited_version(job["id"], first["id"], {"summary": "虚构测试：编辑后的简介"}, ["work", "summary"], {"template_id": "template-02"})
        self.assertNotEqual(updated["id"], first["id"])
        self.assertEqual(updated["number"], 2)
        self.assertEqual(updated["source_version_id"], first["id"])
        self.assertEqual(updated["resume_content"]["summary"], "虚构测试：编辑后的简介")
        self.assertEqual(updated["module_order"], ["work", "summary"])
        versions = self.store.load()["companies"][0]["jobs"][0]["versions"]
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0], first)

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
            self.store.save_edited_version("missing-job", "missing-version", self.content, self.order, self.visual)

    def test_rename_and_delete_company_cascades_jobs_and_versions(self):
        company, job = self.create_job()
        self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        other = self.store.add_company("另一虚构测试公司")
        renamed = self.store.rename_company(company["id"], "改名后的虚构测试公司")
        self.assertEqual(renamed["name"], "改名后的虚构测试公司")
        self.assertEqual(renamed["id"], company["id"])
        self.store.delete_company(company["id"])
        self.assertEqual([row["id"] for row in self.store.load()["companies"]], [other["id"]])
        with self.assertRaises(KeyError):
            self.store.delete_company(company["id"])

    def test_rename_and_delete_job_cascades_versions(self):
        company, job = self.create_job()
        other = self.store.add_job(company["id"], "另一虚构测试岗位", "另一要求")
        self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        renamed = self.store.rename_job(job["id"], "改名后的虚构测试岗位")
        self.assertEqual(renamed["title"], "改名后的虚构测试岗位")
        self.assertEqual(renamed["job_text"], "虚构测试招聘要求")
        self.store.delete_job(job["id"])
        jobs = self.store.load()["companies"][0]["jobs"]
        self.assertEqual([row["id"] for row in jobs], [other["id"]])
        with self.assertRaises(KeyError):
            self.store.rename_job(job["id"], "不存在的岗位")

    def test_delete_versions_keeps_history_and_never_reuses_numbers(self):
        company, job = self.create_job()
        first = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        second = self.store.save_edited_version(job["id"], first["id"], self.content, self.order, self.visual)
        self.store.delete_version(job["id"], second["id"])
        third = self.store.save_edited_version(job["id"], first["id"], self.content, self.order, self.visual)
        self.assertEqual(third["number"], 3)
        self.store.delete_version(job["id"], first["id"])
        self.store.delete_version(job["id"], third["id"])
        reopened = JobRecordStore(Path(self.temp.name))
        saved_job = reopened.load()["companies"][0]["jobs"][0]
        self.assertEqual(saved_job["versions"], [])
        self.assertTrue(saved_job["has_generated"])
        self.assertEqual(saved_job["version_counter"], 3)
        with self.assertRaisesRegex(ValueError, "V1"):
            reopened.create_version(company["id"], job["id"], self.content, self.order, self.visual)

    def test_delete_version_requires_matching_job_and_legacy_versions_infer_history(self):
        company, job = self.create_job()
        other = self.store.add_job(company["id"], "另一虚构测试岗位", "另一要求")
        first = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        state = self.store.load()
        legacy_job = state["companies"][0]["jobs"][0]
        legacy_job.pop("has_generated")
        legacy_job.pop("version_counter")
        self.store.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        restored = self.store.load()["companies"][0]["jobs"][0]
        self.assertTrue(restored["has_generated"])
        self.assertEqual(restored["version_counter"], 1)
        with self.assertRaises(KeyError):
            self.store.delete_version(other["id"], first["id"])
        with self.assertRaises(ValueError):
            self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)

    def test_job_update_revision_and_version_requirement_snapshots(self):
        company, job = self.create_job()
        first = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual,
                                          job_text="虚构测试旧要求")
        changed = self.store.update_job(job["id"], "虚构测试新职位", "虚构测试新要求", 0)
        self.assertEqual(changed["revision"], 1)
        with self.assertRaisesRegex(ValueError, "已被其他操作修改"):
            self.store.update_job(job["id"], "旧页面标题", "旧页面要求", 0)
        with self.assertRaises(ValueError):
            self.store.update_job(job["id"], "虚构测试职位", " ", 1)
        second = self.store.save_edited_version(job["id"], first["id"], self.content, self.order, self.visual)
        self.assertEqual(first["job_text"], "虚构测试旧要求")
        self.assertEqual(second["job_text"], "虚构测试新要求")
        self.assertEqual(second["source_version_id"], first["id"])
        self.assertEqual(second["source_version_number"], 1)
        self.assertEqual(self.store.load()["companies"][0]["jobs"][0]["job_text"], "虚构测试新要求")

    def test_interview_snapshot_survives_version_deletion_and_cascades_with_job(self):
        company, job = self.create_job()
        version = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        session = self.store.create_interview(job["id"], version["id"])
        self.store.update_job(job["id"], "虚构新职位", "虚构测试最新要求", 0)
        turn = {"question": "虚构测试问题", "answer": "虚构测试回答"}
        saved = self.store.update_interview(session["id"], {"turns": [turn], "status": "answered"}, "new")
        self.assertEqual(saved["job_text"], "虚构测试招聘要求")
        self.assertEqual(saved["resume_content"], self.content)
        with self.assertRaisesRegex(ValueError, "状态已更新"):
            self.store.update_interview(session["id"], {"status": "failed"}, "new")
        error = {"code": "ai_error", "message": "虚构测试失败"}
        self.store.update_interview(session["id"], {"status": "failed", "error": error}, "answered")
        reopened = JobRecordStore(Path(self.temp.name))
        self.assertEqual(reopened.get_interview(session["id"])["turns"], [turn])
        self.assertEqual(reopened.get_interview(session["id"])["error"], error)
        self.assertNotIn("turns", reopened.list_interviews(job["id"])[0])
        self.assertNotIn("resume_content", reopened.list_interviews(job["id"])[0])
        self.store.delete_version(job["id"], version["id"])
        self.assertTrue(reopened.get_interview(session["id"])["version_deleted"])
        self.store.delete_job(job["id"])
        self.assertIsNone(reopened.get_interview(session["id"]))

    def test_new_interview_replaces_previous_session_for_same_job(self):
        company, job = self.create_job()
        other = self.store.add_job(company["id"], "另一虚构测试职位", "另一虚构测试要求")
        version = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        other_version = self.store.create_version(company["id"], other["id"], self.content, self.order, self.visual)
        first = self.store.create_interview(job["id"], version["id"])
        unrelated = self.store.create_interview(other["id"], other_version["id"])
        latest = self.store.create_interview(job["id"], version["id"])
        self.assertIsNone(self.store.get_interview(first["id"]))
        self.assertEqual([s["id"] for s in self.store.list_interviews(job["id"])], [latest["id"]])
        self.assertIsNotNone(self.store.get_interview(unrelated["id"]))

    def test_interview_size_and_turn_limit_preserve_previous_state(self):
        company, job = self.create_job()
        version = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        session = self.store.create_interview(job["id"], version["id"])
        with self.assertRaisesRegex(ValueError, "12"):
            self.store.update_interview(session["id"], {"turns": [{}] * 13})
        with self.assertRaisesRegex(ValueError, "350 KB"):
            self.store.update_interview(session["id"], {"turns": [{"answer": "虚构测试" * 60000}]})
        self.assertEqual(self.store.get_interview(session["id"])["turns"], [])

    def test_concurrent_job_update_and_edits_keep_revisions_and_numbers(self):
        company, job = self.create_job()
        first = self.store.create_version(company["id"], job["id"], self.content, self.order, self.visual)
        revision = self.store.load()["companies"][0]["jobs"][0]["revision"]
        def update(_):
            try:
                return self.store.update_job(job["id"], "虚构新职位", "虚构新要求", revision)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            changed = list(pool.map(update, range(2)))
        self.assertEqual(sum(item is not None for item in changed), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            versions = list(pool.map(lambda _: self.store.save_edited_version(
                job["id"], first["id"], self.content, self.order, self.visual), range(8)))
        self.assertEqual(sorted(v["number"] for v in versions), list(range(2, 10)))
        self.assertTrue(all(v["job_text"] == "虚构新要求" for v in versions))


if __name__ == "__main__":
    unittest.main()
