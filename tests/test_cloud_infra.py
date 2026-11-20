"""Deployment helper checks with fictional stack identifiers and no AWS calls."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra"))
import publish_site  # noqa: E402
import cost_estimate  # noqa: E402


class PublishSiteTests(unittest.TestCase):
    def test_http_api_allows_rename_patch_preflight(self):
        template = (ROOT / "infra" / "template.yaml").read_text(encoding="utf-8")
        self.assertRegex(template, r"AllowMethods:\s*\[[^\]]*PATCH[^\]]*\]")

    def test_existing_worker_supports_job_extraction_and_interview_tasks(self):
        """Fictional test: new task kinds reuse the scoped worker and temporary store."""
        template = (ROOT / "infra" / "template.yaml").read_text(encoding="utf-8")
        api = template.split("  ApiFunction:\n", 1)[1].split("  WorkerFunction:\n", 1)[0]
        worker = template.split("  WorkerFunction:\n", 1)[1].split("  ApiLogGroup:\n", 1)[0]
        files = template.split("  TemporaryFiles:\n", 1)[1].split("  OpenAISecret:\n", 1)[0]
        self.assertIn("LambdaInvokePolicy:", api)
        self.assertIn("DynamoDBCrudPolicy:", api)
        self.assertIn("S3CrudPolicy:", api)
        for permission in ("DynamoDBCrudPolicy:", "S3CrudPolicy:", "secretsmanager:GetSecretValue"):
            self.assertIn(permission, worker)
        self.assertIn("Timeout: 900", worker)
        self.assertIn("ExpirationInDays: 2", files)
        self.assertIn("DaysAfterInitiation: 1", files)

    def test_container_copies_only_runtime_inputs(self):
        dockerfile = (ROOT / "Dockerfile.cloud").read_text(encoding="utf-8")
        self.assertNotIn("COPY . ", dockerfile)
        self.assertNotIn("COPY fixtures", dockerfile)
        self.assertNotIn("COPY sample_exports", dockerfile)
        for needed in ("COPY cvbuilder", "COPY templates", "COPY docs/schemas"):
            self.assertIn(needed, dockerfile)

    def test_cost_envelope_is_computed_from_rows(self):
        self.assertEqual(tuple(map(str, cost_estimate.totals())), ("2.11", "8.90"))

    def test_publishes_public_config_without_secret_or_local_data(self):
        outputs = [
            {"OutputKey": key, "OutputValue": value}
            for key, value in {
                "SiteBucket": "fictional-site-bucket",
                "ApiUrl": "https://example.execute-api.ap-southeast-2.amazonaws.com",
                "CognitoIssuer": "https://cognito-idp.ap-southeast-2.amazonaws.com/pool-test",
                "UserPoolClientId": "fictional-client-id",
                "HostedUiDomain": "https://fictional.auth.ap-southeast-2.amazoncognito.com",
                "SiteUrl": "https://fictional.cloudfront.net/",
                "DistributionId": "fictional-distribution",
                "SecretArn": "secret-must-not-be-public",
            }.items()
        ]
        calls = []
        config_text = None

        def fake_aws(*args):
            nonlocal config_text
            calls.append(args)
            if args[:2] == ("cloudformation", "describe-stacks"):
                return json.dumps(outputs)
            if args[:2] == ("s3", "cp") and args[3] == "s3://fictional-site-bucket/static/cloud-config.js":
                config_text = Path(args[2]).read_text(encoding="utf-8")
            return ""

        with patch.object(publish_site, "aws", side_effect=fake_aws), patch.object(sys, "argv", ["publish_site.py", "--stack", "fictional-stack"]):
            publish_site.main()
        self.assertIn("fictional-client-id", config_text)
        self.assertNotIn("secret-must-not-be-public", config_text)
        self.assertTrue(any(x[:2] == ("cloudfront", "create-invalidation") for x in calls))
        self.assertTrue(any(x[:2] == ("s3", "sync") and "--exclude" in x for x in calls))


if __name__ == "__main__":
    unittest.main()
