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
        self.assertEqual(first["version"], "001_initial_mysql")
        self.assertFalse(second["applied"])


if __name__ == "__main__":
    unittest.main()
