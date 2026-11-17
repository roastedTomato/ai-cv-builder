"""Cloud storage contract tests with fictional records and a transactional table fake."""
import copy
import sys
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

from cvbuilder.cloud_storage import CloudStore


class Condition:
    def __init__(self, name, op, value=None):
        self.name, self.op, self.value = name, op, value

    def __and__(self, other):
        return CompoundCondition(self, other)

    def eq(self, value):
        return Condition(self.name, "eq", value)

    def begins_with(self, value):
        return Condition(self.name, "prefix", value)

    def exists(self):
        return Condition(self.name, "exists")

    def not_exists(self):
        return Condition(self.name, "absent")

    def check(self, item):
        if self.op == "eq":
            return item.get(self.name) == self.value
        if self.op == "prefix":
            return str(item.get(self.name, "")).startswith(self.value)
        return (self.name in item) == (self.op == "exists")


class CompoundCondition:
    def __init__(self, left, right):
        self.left, self.right = left, right

    def __and__(self, other):
        return CompoundCondition(self, other)

    def check(self, item):
        return self.left.check(item) and self.right.check(item)


class FakeClientError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeTypeSerializer:
    def serialize(self, value):
        if isinstance(value, str):
            return {"S": value}
        if isinstance(value, bool):
            return {"BOOL": value}
        if isinstance(value, int):
            return {"N": str(value)}
        if value is None:
            return {"NULL": True}
        if isinstance(value, list):
            return {"L": [self.serialize(item) for item in value]}
        if isinstance(value, dict):
            return {"M": {key: self.serialize(item) for key, item in value.items()}}
        raise TypeError(type(value).__name__)


def decode(value):
    kind, content = next(iter(value.items()))
    if kind == "N":
        return int(content)
    if kind == "L":
        return [decode(item) for item in content]
    if kind == "M":
        return {key: decode(item) for key, item in content.items()}
    return None if kind == "NULL" else content


class FakeClient:
    def __init__(self, table):
        self.table = table
        self.exceptions = types.SimpleNamespace(ConditionalCheckFailedException=FakeClientError)

    def transact_write_items(self, TransactItems):
        if "ConditionCheck" in TransactItems[0]:
            check = TransactItems[0]["ConditionCheck"]
            put = TransactItems[1]["Put"]
            with self.table.lock:
                parent = self.table.rows.get(tuple(check["Key"][k] for k in ("pk", "sk")))
                if parent is None or parent.get("deleted"):
                    raise FakeClientError("TransactionCanceledException")
                key = tuple(put["Item"][k] for k in ("pk", "sk"))
                if key in self.table.rows:
                    raise FakeClientError("TransactionCanceledException")
                self.table.rows[key] = copy.deepcopy(put["Item"])
            return
        update = TransactItems[0]["Update"]
        put = TransactItems[1]["Put"]
        self.table.last_transaction = copy.deepcopy(TransactItems)
        key = tuple(update["Key"][k] for k in ("pk", "sk"))
        with self.table.lock:
            current = self.table.rows.get(key)
            values = update["ExpressionAttributeValues"]
            if current is None or current.get("deleted") or current.get("company_id") != values[":company"] or current.get("revision", 0) != values[":revision"]:
                raise FakeClientError("TransactionCanceledException")
            if "attribute_not_exists(version_counter)" in update["ConditionExpression"] and "version_counter" in current:
                raise FakeClientError("TransactionCanceledException")
            if len(TransactItems) == 3:
                check = TransactItems[2]["ConditionCheck"]
                source_key = tuple(check["Key"][k] for k in ("pk", "sk"))
                if source_key not in self.table.rows:
                    raise FakeClientError("TransactionCanceledException")
            version = put["Item"]
            version_key = tuple(version[k] for k in ("pk", "sk"))
            if version_key in self.table.rows:
                raise FakeClientError("TransactionCanceledException")
            current["version_counter"] = values[":number"]
            current["revision"] = values[":next_revision"]
            self.table.rows[version_key] = copy.deepcopy(version)


class FakeTable:
    def __init__(self):
        self.name = "test-table"
        self.rows = {}
        self.last_transaction = None
        self.lock = threading.RLock()
        self.meta = types.SimpleNamespace(client=FakeClient(self))

    @staticmethod
    def key(item):
        return item["pk"], item["sk"]

    def get_item(self, Key, **_):
        with self.lock:
            item = self.rows.get(self.key(Key))
            return {"Item": copy.deepcopy(item)} if item is not None else {}

    def put_item(self, Item, ConditionExpression=None):
        with self.lock:
            old = self.rows.get(self.key(Item), {})
            if ConditionExpression and not ConditionExpression.check(old):
                raise FakeClientError("ConditionalCheckFailedException")
            self.rows[self.key(Item)] = copy.deepcopy(Item)

    def delete_item(self, Key, ConditionExpression=None):
        with self.lock:
            old = self.rows.get(self.key(Key), {})
            if ConditionExpression and not ConditionExpression.check(old):
                raise FakeClientError("ConditionalCheckFailedException")
            self.rows.pop(self.key(Key), None)

    def query(self, KeyConditionExpression, **_):
        with self.lock:
            return {"Items": [copy.deepcopy(item) for item in self.rows.values() if KeyConditionExpression.check(item)]}

    def update_item(self, Key, ExpressionAttributeValues, ConditionExpression, UpdateExpression, **_):
        with self.lock:
            item = self.rows.get(self.key(Key), {})
            if not ConditionExpression.check(item):
                raise FakeClientError("ConditionalCheckFailedException")
            if "#name" in UpdateExpression:
                item["name"] = ExpressionAttributeValues[":name"]
            elif "title" in UpdateExpression:
                item["title"] = ExpressionAttributeValues[":title"]
            elif "deleted" in UpdateExpression:
                item["deleted"] = ExpressionAttributeValues[":deleted"]

    def batch_writer(self):
        table = self

        class Writer:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def delete_item(self, Key):
                table.delete_item(Key)

        return Writer()


class FakeResource:
    def __init__(self):
        self.table = FakeTable()

    def Table(self, name):
        return self.table


class CloudStoreTests(unittest.TestCase):
    def setUp(self):
        conditions = types.ModuleType("boto3.dynamodb.conditions")
        conditions.Key = lambda name: Condition(name, "key")
        conditions.Attr = lambda name: Condition(name, "attr")
        boto3 = types.ModuleType("boto3")
        dynamodb = types.ModuleType("boto3.dynamodb")
        dynamodb_types = types.ModuleType("boto3.dynamodb.types")
        dynamodb_types.TypeSerializer = FakeTypeSerializer
        exceptions = types.ModuleType("botocore.exceptions")
        exceptions.ClientError = FakeClientError
        botocore = types.ModuleType("botocore")
        self.patch_modules = patch.dict(sys.modules, {
            "boto3": boto3, "boto3.dynamodb": dynamodb,
            "boto3.dynamodb.conditions": conditions,
            "boto3.dynamodb.types": dynamodb_types,
            "botocore": botocore, "botocore.exceptions": exceptions,
        })
        self.patch_modules.start()
        self.addCleanup(self.patch_modules.stop)
        self.resource = FakeResource()
        self.a = CloudStore("test-user-a", "test-table", self.resource)
        self.b = CloudStore("test-user-b", "test-table", self.resource)

    def test_resume_and_records_survive_new_instance_and_isolate_users(self):
        resume = {"fixture_note": "虚构测试数据", "personal": {"name": "测试甲"}}
        self.a.save_resume(resume)
        company = self.a.add_company("虚构测试公司")
        job = self.a.add_job(company["id"], "虚构职位", "虚构招聘要求")
        reopened = CloudStore("test-user-a", "test-table", self.resource)
        self.assertEqual(reopened.load_resume(), resume)
        self.assertEqual(reopened.get_job(job["id"])[0]["id"], company["id"])
        self.assertIsNone(self.b.load_resume())
        self.assertEqual(self.b.list_applications(), {"companies": []})
        with self.assertRaises(KeyError):
            self.b.get_job(job["id"])
        with self.assertRaises(KeyError):
            self.b.add_job(company["id"], "职位", "要求")

    def test_first_version_only_once_and_concurrent_edits_are_new_versions(self):
        company = self.a.add_company("虚构测试公司")
        job = self.a.add_job(company["id"], "虚构职位", "虚构要求")
        content = {"summary": "虚构测试内容"}
        first = self.a.create_version(company["id"], job["id"], content, ["summary"],
                                      {"template_id": "template-01"}, initial_only=True)
        self.assertEqual(first["number"], 1)
        self.assertIsNone(first["source_version_id"])
        with self.assertRaises(ValueError):
            self.a.create_version(company["id"], job["id"], content, ["summary"], {}, initial_only=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            versions = list(pool.map(lambda _: self.a.save_edited_version(job["id"], first["id"], content,
                                                                     ["summary"], {"template_id": "template-01"}), range(16)))
        self.assertEqual(sorted(v["number"] for v in versions), list(range(2, 18)))
        self.assertTrue(all(v["source_version_id"] == first["id"] for v in versions))
        transaction = self.resource.table.last_transaction
        self.assertEqual(transaction[0]["Update"]["Key"]["pk"], self.a.pk)
        self.assertIsInstance(transaction[0]["Update"]["ExpressionAttributeValues"][":number"], int)
        self.assertEqual(transaction[1]["Put"]["Item"]["resume_content"], content)
        second = versions[0]
        third = self.a.save_edited_version(job["id"], second["id"], {"summary": "虚构编辑"},
                                           ["summary"], {"template_id": "template-02"})
        self.assertEqual(third["number"], 18)
        self.assertEqual(third["source_version_id"], second["id"])
        saved = self.a.get_job(job["id"])[1]["versions"]
        self.assertEqual(len(saved), 18)
        self.assertEqual(saved[0]["resume_content"], content)
        with self.assertRaises(KeyError):
            self.b.save_edited_version(job["id"], first["id"], content, [], {})
        with self.assertRaises(KeyError):
            self.a.save_edited_version(job["id"], "unknown", content, [], {})

    def test_concurrent_initial_generation_creates_only_v1(self):
        company = self.a.add_company("虚构测试公司")
        job = self.a.add_job(company["id"], "虚构职位", "虚构要求")
        def create():
            try:
                return self.a.create_version(company["id"], job["id"], {"summary": "虚构"},
                                             ["summary"], {}, initial_only=True)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual([v["number"] for v in results if v], [1])
        self.assertEqual(len(self.a.get_job(job["id"])[1]["versions"]), 1)

    def test_edit_accepts_dynamodb_decimal_version_number(self):
        company = self.a.add_company("虚构测试公司")
        job = self.a.add_job(company["id"], "虚构职位", "虚构要求")
        version = self.a.create_version(company["id"], job["id"],
                                        {"summary": "虚构旧内容"}, ["summary"], {})
        key = (self.a.pk, "VERSION#" + job["id"] + "#" + version["id"])
        self.resource.table.rows[key]["number"] = Decimal("1")
        updated = self.a.save_edited_version(job["id"], version["id"],
                                        {"summary": "虚构编辑内容"}, ["summary"], {})
        self.assertEqual(updated["resume_content"], {"summary": "虚构编辑内容"})
        self.assertEqual(updated["number"], 2)
        self.assertEqual(self.a.get_job(job["id"])[1]["versions"][0]["resume_content"],
                         {"summary": "虚构旧内容"})

    def test_task_and_reset_are_user_scoped(self):
        task = {"id": "fictional-task", "kind": "import", "status": "pending", "input": {"fixture_note": "虚构测试数据"}}
        self.a.create_task(task)
        self.assertIsNone(self.b.get_task(task["id"]))
        self.assertEqual(self.a.update_task(task["id"], {"status": "running"})["status"], "running")
        with self.assertRaises(ValueError):
            self.a.update_task(task["id"], {"status": "running"})
        self.assertEqual(self.a.update_task(task["id"], {"status": "done"})["status"], "done")
        with self.assertRaises(KeyError):
            self.b.update_task(task["id"], {"status": "failed"})
        with self.assertRaises(ValueError):
            self.a.update_task(task["id"], {"user_id": "test-user-b"})
        self.b.save_resume({"fixture_note": "虚构测试数据"})
        self.a.reset()
        self.assertIsNone(self.a.get_task(task["id"]))
        self.assertEqual(self.b.load_resume(), {"fixture_note": "虚构测试数据"})

    def test_oversized_items_are_rejected_before_write(self):
        huge = "虚构测试内容" * 40000
        with self.assertRaisesRegex(ValueError, "350 KB"):
            self.a.save_resume({"fixture_note": "虚构测试数据", "text": huge})
        company = self.a.add_company("虚构测试公司")
        with self.assertRaisesRegex(ValueError, "350 KB"):
            self.a.add_job(company["id"], "虚构职位", huge)
        job = self.a.add_job(company["id"], "虚构职位", "虚构要求")
        with self.assertRaisesRegex(ValueError, "350 KB"):
            self.a.create_version(company["id"], job["id"], {"summary": huge},
                                  ["summary"], {"template_id": "template-01"})
        version = self.a.create_version(company["id"], job["id"],
                                        {"summary": "虚构测试短内容"}, ["summary"], {})
        with self.assertRaisesRegex(ValueError, "350 KB"):
            self.a.save_edited_version(job["id"], version["id"], {"summary": huge},
                                  ["summary"], {})
        self.assertEqual(self.a.get_job(job["id"])[1]["versions"][0]["resume_content"],
                         {"summary": "虚构测试短内容"})
        with self.assertRaisesRegex(ValueError, "350 KB"):
            self.a.create_task({"id": "oversized", "kind": "import", "status": "queued",
                                "input": {"fixture_note": "虚构测试数据", "text": huge}})
        self.assertIsNone(self.a.get_task("oversized"))
        self.assertEqual(len(self.a.get_job(job["id"])[1]["versions"]), 1)

    def test_rename_and_delete_are_scoped_and_cascade(self):
        company = self.a.add_company("虚构旧公司")
        job = self.a.add_job(company["id"], "虚构旧职位", "虚构要求")
        version = self.a.create_version(company["id"], job["id"], {"summary": "虚构"}, [], {}, initial_only=True)
        for action in (
            lambda: self.b.rename_company(company["id"], "其他名字"),
            lambda: self.b.delete_company(company["id"]),
            lambda: self.b.rename_job(job["id"], "其他名字"),
            lambda: self.b.delete_job(job["id"]),
            lambda: self.b.delete_version(job["id"], version["id"]),
        ):
            with self.assertRaises(KeyError):
                action()
        for action in (
            lambda: self.a.rename_company(company["id"], " "),
            lambda: self.a.rename_job(job["id"], " "),
        ):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(self.a.rename_company(company["id"], "虚构新公司")["name"], "虚构新公司")
        self.assertEqual(self.a.rename_job(job["id"], "虚构新职位")["title"], "虚构新职位")
        self.assertEqual(self.a.list_applications()["companies"][0]["jobs"][0]["title"], "虚构新职位")
        self.a.delete_company(company["id"])
        self.assertEqual(self.a.list_applications(), {"companies": []})
        self.assertFalse(any(key[0] == self.a.pk and key[1].startswith(("COMPANY#", "JOB#", "VERSION#"))
                             for key in self.resource.table.rows))
        with self.assertRaises(KeyError):
            self.a.get_job(job["id"])

    def test_delete_job_and_version_keep_history_counter(self):
        company = self.a.add_company("虚构公司")
        job = self.a.add_job(company["id"], "虚构职位", "虚构要求")
        first = self.a.create_version(company["id"], job["id"], {"summary": "虚构 V1"}, [], {}, initial_only=True)
        second = self.a.save_edited_version(job["id"], first["id"], {"summary": "虚构 V2"}, [], {})
        self.a.delete_version(job["id"], second["id"])
        third = self.a.save_edited_version(job["id"], first["id"], {"summary": "虚构 V3"}, [], {})
        self.assertEqual(third["number"], 3)
        self.a.delete_version(job["id"], third["id"])
        self.a.delete_version(job["id"], first["id"])
        info = self.a.get_job(job["id"])[1]
        self.assertTrue(info["has_generated"])
        self.assertEqual(info["versions"], [])
        self.assertTrue(self.a.list_applications()["companies"][0]["jobs"][0]["has_generated"])
        with self.assertRaises(ValueError):
            self.a.create_version(company["id"], job["id"], {"summary": "重复"}, [], {}, initial_only=True)
        # A future version can only derive from an existing source; deleting a
        # version never makes its old number available again.
        with self.assertRaises(KeyError):
            self.a.save_edited_version(job["id"], first["id"], {"summary": "虚构"}, [], {})
        another = self.a.add_job(company["id"], "另一个虚构职位", "要求")
        child = self.a.create_version(company["id"], another["id"], {"summary": "虚构"}, [], {}, initial_only=True)
        self.a.delete_job(another["id"])
        self.assertEqual(len(self.a.list_applications()["companies"][0]["jobs"]), 1)
        self.assertIsNone(self.a._get("VERSION#" + another["id"] + "#" + child["id"]))

    def test_deleted_parent_blocks_new_child_and_version(self):
        company = self.a.add_company("虚构公司")
        job = self.a.add_job(company["id"], "虚构职位", "虚构要求")
        self.a._mark_deleted("COMPANY#" + company["id"], company["id"])
        with self.assertRaises(KeyError):
            self.a.add_job(company["id"], "并发新增", "要求")
        self.a._mark_deleted("JOB#" + job["id"], job["id"])
        with self.assertRaises(KeyError):
            self.a.create_version(company["id"], job["id"], {"summary": "虚构"}, [], {})


if __name__ == "__main__":
    unittest.main()
