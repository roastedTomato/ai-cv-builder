import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os

from cvbuilder.storage import ResumeStore, data_directory


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = ResumeStore(Path(self.temp.name))
        self.resume = {"fixture_note": "虚构测试数据，不对应真实个人。", "resume_id": "test-resume", "version": "1", "personal": {"name": "虚构测试者", "email": "test@example.invalid", "phone": "000", "location": "测试城市", "summary": ""}, "education": [], "work": [], "projects": [], "skills": [], "certificates": []}

    def test_save_load_delete_and_reset(self):
        self.assertIsNone(self.store.load())
        self.store.save(self.resume)
        self.assertEqual(self.store.load(), self.resume)
        self.store.delete(); self.assertIsNone(self.store.load())
        self.store.save(self.resume); self.store.reset(); self.assertFalse(self.store.path.exists())

    def test_failed_replace_preserves_previous_data(self):
        self.store.save(self.resume)
        with patch("cvbuilder.storage.os.replace", side_effect=OSError("simulated")):
            with self.assertRaises(OSError): self.store.save({**self.resume, "version": "2"})
        self.assertEqual(self.store.load(), self.resume)

    def test_data_path_is_fixed_user_application_directory(self):
        self.assertEqual(data_directory().name, "ai-cv-builder")
        if os.name != "nt" and __import__("sys").platform != "darwin":
            with patch.dict(os.environ, {"XDG_DATA_HOME": self.temp.name}):
                self.assertEqual(data_directory(), Path(self.temp.name) / "ai-cv-builder")

