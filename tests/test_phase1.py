import json
import io
from contextlib import redirect_stderr
import os
from pathlib import Path

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cvbuilder.cli import main, validate
from cvbuilder.core import ValidationError, analyze_match, check_generated_facts, check_requirement_gaps, normalize_generated_resume, populate_requirement_gaps
from cvbuilder.templates import create_templates, fill_template, convert_to_pdf, find_soffice
from cvbuilder.ai import _request
from cvbuilder.ai import generate_resume

ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def remove_claim_source(generated, source_id):
    claims = list(generated.get("summary", []))
    claims.extend(claim for section in ("education", "work", "projects", "skills", "certificates")
                  for entry in generated.get(section, []) for claim in entry.get("bullets", []))
    for claim in claims:
        claim["source_ids"] = [sid for sid in claim["source_ids"] if sid != source_id]


class PhaseOneTests(unittest.TestCase):
    def test_sample_data_passes_schemas(self):
        validate(fixture("base-resume.sample.json"), "base")
        validate(fixture("job-requirement.sample.json"), "job")
        validate(fixture("generated-resume.sample.json"), "generated")
        validate(fixture("long-resume.sample.json"), "base")

    def test_match_identifies_missing_capability_as_gap(self):
        result = analyze_match(fixture("base-resume.sample.json"), fixture("job-requirement.sample.json"))
        self.assertIn("Python", result["matched_requirements"])
        self.assertIn("SQL", result["matched_requirements"])
        self.assertIn("数据可视化", result["gaps"])
        self.assertIn("统计学背景", result["gaps"])

    def test_generated_sample_has_traceable_sources_and_no_new_numbers(self):
        base = fixture("base-resume.sample.json")
        req = fixture("job-requirement.sample.json")
        generated = fixture("generated-resume.sample.json")
        check_generated_facts(base, generated)
        check_requirement_gaps(base, req, generated)

    def test_fact_guard_accepts_source_backed_degree_and_major_format(self):
        generated = fixture("generated-resume.sample.json")
        generated["education"][0]["title"] = "本科｜计算机科学"
        normalize_generated_resume(fixture("base-resume.sample.json"), generated)
        check_generated_facts(fixture("base-resume.sample.json"), generated)
        self.assertEqual(generated["education"][0]["title"], "计算机科学（本科）")

    def test_fact_guard_rejects_unsupported_education_title(self):
        generated = fixture("generated-resume.sample.json")
        generated["education"][0]["title"] = "硕士｜计算机科学"
        with self.assertRaisesRegex(ValidationError, "标题与来源不一致"):
            check_generated_facts(fixture("base-resume.sample.json"), generated)

    def test_model_combined_project_title_is_normalized_from_source(self):
        base = fixture("base-resume.sample.json")
        generated = fixture("generated-resume.sample.json")
        project = generated["projects"][0]
        project["title"] = "课程预约小程序（测试项目）｜开发成员"
        project["organization"] = "开发成员"
        normalize_generated_resume(base, generated)
        check_generated_facts(base, generated)
        self.assertEqual(project["title"], "课程预约小程序（测试项目）")
        self.assertEqual(project["organization"], "开发成员")

    def test_generate_resume_normalizes_project_title_before_fact_check(self):
        base = fixture("base-resume.sample.json")
        req = fixture("job-requirement.sample.json")
        model_result = fixture("generated-resume.sample.json")
        model_result["projects"][0]["title"] = "课程预约小程序（测试项目）｜开发成员"
        model_result["projects"][0]["organization"] = "开发成员"
        model_result["education"][0]["title"] = "本科，计算机科学"
        model_result["gaps"] = ["可视化经验及统计背景待确认"]
        with patch("cvbuilder.ai._request", return_value=model_result):
            result = generate_resume(base, req)
        self.assertEqual(result["projects"][0]["title"], "课程预约小程序（测试项目）")
        self.assertEqual(result["projects"][0]["organization"], "开发成员")
        self.assertEqual(result["education"][0]["title"], "计算机科学（本科）")
        self.assertEqual(result["gaps"], ["数据可视化", "统计学背景"])

    def test_generate_request_redacts_contact_and_restores_it_locally(self):
        base = fixture("base-resume.sample.json")
        req = fixture("job-requirement.sample.json")
        model_result = fixture("generated-resume.sample.json")
        model_result["personal"] = {key: "" for key in ("name", "email", "phone", "location")}
        seen = {}
        def fake_request(name, schema, instructions, payload):
            seen.update(payload)
            return model_result
        with patch("cvbuilder.ai._request", side_effect=fake_request):
            result = generate_resume(base, req)
        self.assertEqual(result["personal"], {key: base["personal"][key] for key in ("name", "email", "phone", "location")})
        self.assertEqual(seen["base_resume"]["personal"]["name"], "")
        self.assertEqual(seen["base_resume"]["personal"]["email"], "")
        self.assertEqual(base["personal"]["name"], result["personal"]["name"])

    def test_skill_using_project_source_is_relinked_to_explicit_skill_record(self):
        base = fixture("base-resume.sample.json")
        generated = fixture("generated-resume.sample.json")
        skill = generated["skills"][0]
        skill.update({
            "source_id": "project-001", "title": "Python", "organization": "项目使用",
            "start_date": "2021-10", "end_date": "2022-04",
            "bullets": [{"text": "Python（项目使用）", "content_type": "rewrite", "source_ids": ["project-001"]}],
        })
        normalize_generated_resume(base, generated)
        check_generated_facts(base, generated)
        self.assertEqual(skill["source_id"], "skill-001")
        self.assertEqual(skill["organization"], "项目使用")
        self.assertEqual(skill["start_date"], "")
        self.assertEqual(skill["bullets"], [])

    def test_project_technology_can_support_skill_when_no_skill_record_exists(self):
        base = fixture("base-resume.sample.json")
        base["skills"] = [skill for skill in base["skills"] if skill["name"] != "Python"]
        generated = fixture("generated-resume.sample.json")
        remove_claim_source(generated, "skill-001")
        entry = generated["skills"][0]
        entry.update({"source_id": "project-001", "title": "Python", "organization": "", "start_date": "", "end_date": "", "bullets": []})
        normalize_generated_resume(base, generated)
        check_generated_facts(base, generated)
        self.assertEqual(entry["source_id"], "project-001")
        self.assertEqual(entry["title"], "Python")

    def test_project_source_cannot_support_unlisted_skill(self):
        generated = fixture("generated-resume.sample.json")
        generated["skills"][0].update({"source_id": "project-001", "title": "Go"})
        normalize_generated_resume(fixture("base-resume.sample.json"), generated)
        with self.assertRaisesRegex(ValidationError, "其他模块的来源 ID|没有来源支持"):
            check_generated_facts(fixture("base-resume.sample.json"), generated)

    def test_education_title_variants_normalize_only_exact_source_parts(self):
        base = fixture("base-resume.sample.json")
        accepted = ["本科，计算机科学", "计算机科学, 本科", "本科、计算机科学", "本科 | 计算机科学", "本科 - 计算机科学", "本科 计算机科学"]
        for title in accepted:
            with self.subTest(title=title):
                generated = fixture("generated-resume.sample.json")
                generated["education"][0]["title"] = title
                normalize_generated_resume(base, generated)
                self.assertEqual(generated["education"][0]["title"], "计算机科学（本科）")
                check_generated_facts(base, generated)
        rejected = ["硕士，计算机科学", "本科，数据科学", "本科，计算机科学，CFA"]
        for title in rejected:
            with self.subTest(title=title):
                generated = fixture("generated-resume.sample.json")
                generated["education"][0]["title"] = title
                normalize_generated_resume(base, generated)
                with self.assertRaisesRegex(ValidationError, "标题与来源不一致"):
                    check_generated_facts(base, generated)

    def test_requirement_gaps_are_exact_terms_from_structured_jd(self):
        generated = fixture("generated-resume.sample.json")
        generated["gaps"] = ["模型自行改写的缺口措辞"]
        populate_requirement_gaps(fixture("base-resume.sample.json"), fixture("job-requirement.sample.json"), generated)
        self.assertEqual(generated["gaps"], ["数据可视化", "统计学背景"])

    def test_project_normalizer_rejects_invented_role(self):
        base = fixture("base-resume.sample.json")
        generated = fixture("generated-resume.sample.json")
        project = generated["projects"][0]
        project["title"] = "课程预约小程序（测试项目）｜高级负责人"
        project["organization"] = "高级负责人"
        with self.assertRaisesRegex(ValidationError, "标题与来源不一致"):
            normalize_generated_resume(base, generated)
            check_generated_facts(base, generated)

    def test_fact_guard_rejects_gap_skill_in_resume(self):
        with self.assertRaisesRegex(ValidationError, "不能直接写入"):
            check_requirement_gaps(fixture("base-resume.sample.json"), fixture("job-requirement.sample.json"), {
                **fixture("generated-resume.sample.json"),
                "summary": [{"text": "具备数据可视化能力。", "source_ids": ["work-001"]}],
            })

    def test_fact_guard_rejects_new_metric(self):
        resume = fixture("base-resume.sample.json")
        generated = fixture("generated-resume.sample.json")
        generated["summary"][0]["text"] = "实现业务增长 99%。"
        with self.assertRaisesRegex(ValidationError, "数字"):
            check_generated_facts(resume, generated)

    def test_fact_guard_rejects_unknown_provenance(self):
        resume = fixture("base-resume.sample.json")
        generated = fixture("generated-resume.sample.json")
        generated["summary"][0]["source_ids"] = ["invented-id"]
        with self.assertRaisesRegex(ValidationError, "未知来源"):
            check_generated_facts(resume, generated)

    def test_model_response_with_invalid_json_returns_clear_error(self):
        fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text="not-json")))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            with self.assertRaisesRegex(RuntimeError, "不是有效 JSON"):
                _request("job_requirement", "job-requirement.schema.json", "instructions", {})

    def test_model_request_uses_structured_outputs_without_storage(self):
        result = fixture("job-requirement.sample.json")
        seen = {}
        def create(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(output_text=json.dumps(result, ensure_ascii=False))
        fake = SimpleNamespace(responses=SimpleNamespace(create=create))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            self.assertEqual(_request("job_requirement", "job-requirement.schema.json", "instructions", {}), result)
        self.assertFalse(seen["store"])
        self.assertEqual(seen["text"]["format"]["type"], "json_schema")
        self.assertTrue(seen["text"]["format"]["strict"])
        self.assertNotIn("$schema", seen["text"]["format"]["schema"])

    def test_web_generation_skips_local_schema_and_fact_gates(self):
        from cvbuilder.ai import generate_resume_unchecked
        base = fixture("base-resume.sample.json")
        requirement = fixture("job-requirement.sample.json")
        raw = {"summary": [{"text": "虚构测试：未经核验的 999 项成果"}]}
        fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(raw, ensure_ascii=False))))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            result = generate_resume_unchecked(base, requirement)
        self.assertEqual(result["summary"], raw["summary"])
        self.assertEqual(result["personal"]["name"], base["personal"]["name"])

    def test_web_generation_from_raw_job_uses_one_structured_request_with_full_resume(self):
        from cvbuilder.ai import generate_resume_from_job_unchecked
        base = fixture("base-resume.sample.json")
        calls = []
        raw = {"summary": [{"text": "虚构测试：未经核验的 999 项成果"}]}
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text=json.dumps(raw, ensure_ascii=False))
        fake = SimpleNamespace(responses=SimpleNamespace(create=create))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            result = generate_resume_from_job_unchecked(base, "虚构测试岗位要求")
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["store"])
        self.assertEqual(calls[0]["text"]["format"]["type"], "json_schema")
        payload = json.loads(calls[0]["input"])
        self.assertEqual(payload["base_resume"]["personal"], base["personal"])
        self.assertEqual(payload["job_requirement"]["job_text"], "虚构测试岗位要求")
        self.assertEqual(result["summary"], raw["summary"])

    def test_import_structure_sends_full_text_and_contacts(self):
        from cvbuilder.ai import structure_resume
        personal = {"name": "虚构测试人", "email": "test@example.com", "phone": "13800138000", "location": "测试市"}
        seen = {}
        with patch("cvbuilder.ai._request", side_effect=lambda *args, **kwargs: seen.update(kwargs.get("payload", args[3])) or {}):
            structure_resume("姓名：虚构测试人\n邮箱：test@example.com", personal)
        self.assertEqual(seen["personal"], personal)
        self.assertIn("test@example.com", seen["resume_text"])

    def test_structured_output_schema_removes_unsupported_keywords_recursively(self):
        from cvbuilder.ai import _structured_output_schema
        schema = _structured_output_schema(json.loads((ROOT / "docs/schemas/base-resume.schema.json").read_text()))
        rendered = json.dumps(schema)
        for keyword in ('"const"', '"minLength"', '"minItems"', '"$schema"', '"$id"'):
            self.assertNotIn(keyword, rendered)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertFalse(schema["properties"]["personal"]["additionalProperties"])
        self.assertEqual(set(schema["properties"]["personal"]["required"]), set(schema["properties"]["personal"]["properties"]))
        self.assertNotIn("fixture_note", schema["properties"])
        self.assertEqual(schema["properties"]["personal"]["properties"]["summary"]["type"], ["string", "null"])

    def test_optional_null_fields_are_removed_before_local_schema_validation(self):
        from cvbuilder.ai import _request
        result = fixture("base-resume.sample.json")
        result.pop("fixture_note", None)
        result["personal"]["summary"] = None
        fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(result, ensure_ascii=False))))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            normalized = _request("base_resume", "base-resume.schema.json", "instructions", {})
        self.assertNotIn("fixture_note", normalized)
        self.assertNotIn("summary", normalized["personal"])

    def test_import_assigns_nonempty_local_ids_before_schema_validation(self):
        from cvbuilder.ai import _request
        result = fixture("base-resume.sample.json")
        result.pop("fixture_note", None)
        result["resume_id"] = ""
        result["version"] = ""
        for section in ("education", "work", "projects", "skills", "certificates"):
            for item in result[section]:
                item["id"] = ""
        fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(result, ensure_ascii=False))))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            normalized = _request("base_resume", "base-resume.schema.json", "instructions", {})
        validate(normalized, "base")
        self.assertTrue(normalized["resume_id"].startswith("resume-"))
        all_ids = [item["id"] for section in ("education", "work", "projects", "skills", "certificates") for item in normalized[section]]
        self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_import_completes_missing_work_fields_as_reviewable_blanks(self):
        from cvbuilder.ai import _request
        result = {"personal": {"name": "", "email": "", "phone": "", "location": ""},
                  "work": [{"employer": "虚构测试公司", "title": "数据助理"}]}
        fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(result, ensure_ascii=False))))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            normalized = _request("base_resume", "base-resume.schema.json", "instructions", {})
        validate(normalized, "base")
        self.assertEqual(normalized["work"][0]["employer"], "虚构测试公司")
        self.assertEqual(normalized["work"][0]["description"], "")
        self.assertEqual(normalized["work"][0]["achievements"], [])
        self.assertTrue(normalized["work"][0]["id"])

    def test_generation_completes_only_source_backed_missing_item_fields(self):
        from cvbuilder.ai import _request
        base = fixture("base-resume.sample.json")
        base["certificates"] = [{"id": "certificate-test-001", "name": "虚构测试证书", "issuer": "测试机构", "date": "2022"}]
        requirement = fixture("job-requirement.sample.json")
        result = fixture("generated-resume.sample.json")
        for section in ("education", "work", "projects", "skills"):
            for key in ("title", "organization", "start_date", "end_date", "bullets"):
                result[section][0].pop(key, None)
        result["certificates"] = [{"source_id": "certificate-test-001"}]
        fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(result, ensure_ascii=False))))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            normalized = _request("generated_resume", "generated-resume.schema.json", "instructions", {"base_resume": base, "job_requirement": requirement})
        validate(normalized, "generated")
        check_generated_facts(base, normalized)
        self.assertEqual(normalized["work"][0]["title"], base["work"][0]["title"])
        self.assertEqual(normalized["certificates"][0]["title"], "虚构测试证书")

    def test_generation_restores_fixed_source_fields_but_rejects_new_claim_numbers(self):
        from cvbuilder.ai import _request
        base = fixture("base-resume.sample.json")
        requirement = fixture("job-requirement.sample.json")
        result = fixture("generated-resume.sample.json")
        result["work"][0].update({"title": "虚构新职位", "organization": "虚构新公司", "start_date": "2099-01"})
        fake = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(result, ensure_ascii=False))))
        with patch("cvbuilder.ai._client", return_value=(fake, "test-model")):
            normalized = _request("generated_resume", "generated-resume.schema.json", "instructions", {"base_resume": base, "job_requirement": requirement})
        self.assertEqual(normalized["work"][0]["title"], base["work"][0]["title"])
        self.assertEqual(normalized["work"][0]["organization"], base["work"][0]["employer"])
        self.assertEqual(normalized["work"][0]["start_date"], base["work"][0]["start_date"])
        check_generated_facts(base, normalized)
        normalized["work"][0]["bullets"][0]["text"] = "提升业务 999%。"
        normalized["work"][0]["bullets"][0]["content_type"] = "rewrite"
        with self.assertRaisesRegex(ValidationError, "数字"):
            check_generated_facts(base, normalized)

    def test_cli_redacts_api_error_details(self):
        class FakeAuthenticationError(Exception):
            status_code = 401
            code = "invalid_api_key"

        err = FakeAuthenticationError("provider text may include sensitive-credential-fragment")
        stderr = io.StringIO()
        with patch("cvbuilder.cli.extract_requirement", side_effect=err), redirect_stderr(stderr):
            result = main(["parse-job", str(ROOT / "fixtures/job-requirement.sample.txt"), "--id", "test-job-001", "--output", "/tmp/unused-job.json"])
        self.assertEqual(result, 2)
        self.assertNotIn("sensitive-credential-fragment", stderr.getvalue())
        self.assertIn("401", stderr.getvalue())

    def test_template_export_repeats_items_and_hides_empty_module(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            templates = create_templates(tmp_path / "templates")
            self.assertEqual(len(templates), 3)
            generated = fixture("generated-resume.sample.json")
            generated["skills"][0]["bullets"] = [{"text": "Python（项目使用）", "content_type": "rewrite", "source_ids": ["skill-001"]}]
            output = fill_template(templates[0], generated, tmp_path / "resume.docx")
            text = "\n".join(p.text for p in __import__("docx").Document(output).paragraphs)
            self.assertEqual(text.count("示例软件工作室"), 1)
            self.assertEqual(text.count("虚构数据实验室"), 1)
            self.assertNotIn("证书", text)
            self.assertNotIn("{{", text)
            self.assertIn("Python · 项目使用", text)
            self.assertNotIn("Python（项目使用）", text)

    def test_soffice_path_override_is_explicit_and_validated(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            executable = Path(folder) / "soffice"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            with patch.dict(os.environ, {"SOFFICE_PATH": str(executable)}):
                self.assertEqual(find_soffice(), str(executable))
            with patch.dict(os.environ, {"SOFFICE_PATH": str(executable) + ".missing"}):
                self.assertIsNone(find_soffice())

    def test_pdf_export_runs_configured_soffice_and_checks_output(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            source = Path(folder) / "resume.docx"
            source.write_bytes(b"fake docx")
            observed = {}
            def fake_run(args, **kwargs):
                observed["args"] = args
                observed["kwargs"] = kwargs
                output = Path(args[args.index("--outdir") + 1]) / "resume.pdf"
                output.write_bytes(b"%PDF-test")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("cvbuilder.templates.find_soffice", return_value="/fake/soffice"), patch("cvbuilder.templates.subprocess.run", side_effect=fake_run):
                result = convert_to_pdf(source)
            self.assertEqual(result, Path(folder) / "resume.pdf")
            self.assertEqual(observed["args"][0], "/fake/soffice")
            self.assertIn("--headless", observed["args"])
            self.assertIn("--convert-to", observed["args"])
            self.assertTrue(Path(observed["kwargs"]["env"]["FONTCONFIG_FILE"]).name == "fonts.conf")

    def test_pdf_export_fails_clearly_when_libreoffice_is_missing(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            source = Path(folder) / "resume.docx"
            source.write_bytes(b"fake docx")
            with patch.dict(os.environ, {"SOFFICE_PATH": ""}), patch("cvbuilder.templates.find_soffice", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "未找到 LibreOffice"):
                    convert_to_pdf(source)

    def test_project_name_and_role_render_once_after_normalization(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            base = fixture("base-resume.sample.json")
            generated = fixture("generated-resume.sample.json")
            generated["projects"][0]["title"] = "课程预约小程序（测试项目）｜开发成员"
            generated["projects"][0]["organization"] = "开发成员"
            normalize_generated_resume(base, generated)
            check_generated_facts(base, generated)
            template = create_templates(tmp_path / "templates")[0]
            output = fill_template(template, generated, tmp_path / "normalized.docx")
            text = "\n".join(p.text for p in __import__("docx").Document(output).paragraphs)
            self.assertEqual(text.count("课程预约小程序（测试项目）"), 1)
            self.assertEqual(text.count("开发成员"), 1)
