"""Publish only the static UI and generated, account-specific public config."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def aws(*args: str) -> str:
    return subprocess.check_output(["aws", *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack", required=True)
    parser.add_argument("--region", default="ap-southeast-2")
    args = parser.parse_args()
    raw = aws("cloudformation", "describe-stacks", "--stack-name", args.stack,
              "--region", args.region, "--query", "Stacks[0].Outputs", "--output", "json")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in json.loads(raw)}
    required = {"SiteBucket", "ApiUrl", "CognitoIssuer", "UserPoolClientId", "HostedUiDomain", "SiteUrl", "DistributionId"}
    if required - outputs.keys():
        raise SystemExit(f"Missing stack outputs: {sorted(required - outputs.keys())}")
    bucket = outputs["SiteBucket"]
    aws("s3", "cp", str(ROOT / "web" / "templates" / "index.html"),
        f"s3://{bucket}/index.html", "--region", args.region,
        "--content-type", "text/html; charset=utf-8")
    aws("s3", "sync", str(ROOT / "web" / "static"), f"s3://{bucket}/static/",
        "--region", args.region, "--exclude", "cloud-config.js")
    config = {
        "apiBase": outputs["ApiUrl"],
        "issuer": outputs["CognitoIssuer"],
        "clientId": outputs["UserPoolClientId"],
        "hostedUiDomain": outputs["HostedUiDomain"],
        "redirectUri": outputs["SiteUrl"],
    }
    with tempfile.TemporaryDirectory(prefix="cvbuilder-config-") as folder:
        path = Path(folder) / "cloud-config.js"
        path.write_text("window.CV_CLOUD_CONFIG = " + json.dumps(config, ensure_ascii=True) + ";\n", encoding="utf-8")
        aws("s3", "cp", str(path), f"s3://{bucket}/static/cloud-config.js",
            "--region", args.region, "--content-type", "application/javascript; charset=utf-8",
            "--cache-control", "no-store")
    aws("cloudfront", "create-invalidation", "--distribution-id", outputs["DistributionId"],
        "--paths", "/index.html", "/static/*", "--region", args.region)
    print(outputs["SiteUrl"])


if __name__ == "__main__":
    main()
