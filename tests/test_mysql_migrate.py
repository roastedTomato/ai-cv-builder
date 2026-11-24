"""Migration tests run only against an isolated fictional MySQL test database."""
import os
import unittest


@unittest.skipUnless(os.environ.get("CV_MYSQL_TEST_DATABASE"), "set CV_MYSQL_TEST_DATABASE for MySQL migration tests")
class MySQLMigrationTests(unittest.TestCase):
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

    def test_migration_is_repeatable(self):
        from cvbuilder.mysql_migrate import handler
        first = handler({}, None)
        second = handler({}, None)
        self.assertEqual(first["version"], "004_resume_version_saved_at")
        self.assertFalse(second["applied"])
        from cvbuilder.mysql_storage import MySQLStore
        with MySQLStore("fictional-migration-check")._transaction() as cursor:
            cursor.execute("SELECT version FROM schema_migrations ORDER BY version")
            self.assertEqual([row["version"] for row in cursor.fetchall()],
                             ["001_initial_mysql", "002_interview_answered_at", "003_job_resume_draft",
                              "004_resume_version_saved_at"])
            cursor.execute("SHOW COLUMNS FROM interview_turns LIKE 'answered_at'")
            self.assertIsNotNone(cursor.fetchone())


if __name__ == "__main__":
    unittest.main()
