from __future__ import annotations

import re
from typing import Any


class ValidationError(ValueError):
    pass


def all_source_text(resume: dict[str, Any]) -> str:
    chunks = [str(v) for v in resume.get("personal", {}).values()]
    for section in ("education", "work", "projects", "skills", "certificates"):
        for item in resume.get(section, []):
            chunks.extend(str(v) for v in item.values())
    return "\n".join(chunks)


def source_map(resume: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        str(item["id"]): (section, item)
        for section in ("education", "work", "projects", "skills", "certificates")
        for item in resume.get(section, [])
    }


def _normalized_education_title(title: str, degree: str, major: str) -> str | None:
    """Return canonical source-backed text for a degree/major pair, else None."""
    canonical = f"{major}（{degree}）"
    if title in (degree, major):
        return title
    if title in (canonical, f"{major}({degree})"):
        return canonical

    # Accept common separators only when the two trimmed pieces exactly match
    # the source degree and major, in either order. Extra words or pieces fail.
    parts = re.split(r"\s*(?:[｜|，,、·:：/／\-—])\s*", title.strip())
    if len(parts) == 2 and {part.strip() for part in parts} == {degree, major}:
        return canonical
    if re.fullmatch(re.escape(degree) + r"\s+" + re.escape(major), title.strip()) or re.fullmatch(
        re.escape(major) + r"\s+" + re.escape(degree), title.strip()
    ):
        return canonical
    return None


def normalize_generated_resume(resume: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize model-formatted project and education titles from source fields."""
    sources = source_map(resume)
    skill_sources = {str(item.get("name", "")).casefold(): item for item in resume.get("skills", []) if item.get("name")}
    technology_sources = {}
    for project in resume.get("projects", []):
        for technology in project.get("technologies", []):
            technology_sources.setdefault(str(technology).casefold(), project)

    for entry in generated.get("education", []):
        source_id = entry.get("source_id")
        if source_id not in sources or sources[source_id][0] != "education":
            continue
        source = sources[source_id][1]
        canonical = _normalized_education_title(
            entry.get("title", ""), source.get("degree", ""), source.get("major", "")
        )
        if canonical is not None:
            entry["title"] = canonical

    for entry in generated.get("projects", []):
        source_id = entry.get("source_id")
        if source_id not in sources or sources[source_id][0] != "projects":
            continue
        source = sources[source_id][1]
        name, role = source.get("name", ""), source.get("role", "")
        if name and role and entry.get("title") == f"{name}｜{role}":
            current_org = entry.get("organization", "")
            if current_org not in ("", role):
                raise ValidationError(f"项目角色与来源不一致: {current_org}")
            entry["title"] = name
            entry["organization"] = role

    # Skills may be supported either by the explicit skills module or by an
    # exact technology listed on a source project. Prefer the canonical skill
    # record when one exists; discard redundant skill bullets already rendered
    # by name/level so they cannot duplicate or drift from those source fields.
    for entry in generated.get("skills", []):
        title_key = str(entry.get("title", "")).casefold()
        skill = skill_sources.get(title_key)
        if skill:
            entry["source_id"] = skill["id"]
            entry["title"] = skill["name"]
            entry["organization"] = skill.get("level", "")
        else:
            project = technology_sources.get(title_key)
            if project:
                entry["source_id"] = project["id"]
                entry["title"] = next(t for t in project.get("technologies", []) if str(t).casefold() == title_key)
                entry["organization"] = ""
        entry["start_date"] = ""
        entry["end_date"] = ""
        entry["bullets"] = []
    return generated


def check_generated_facts(resume: dict[str, Any], generated: dict[str, Any]) -> None:
    """Fail closed for unknown provenance IDs and newly introduced numeric claims."""
    sources = source_map(resume)
    valid_ids = set(sources)
    raw = all_source_text(resume)
    raw_numbers = set(re.findall(r"\d+(?:[,.]\d+)*(?:%|％)?", raw))
    if generated.get("personal") != {key: resume["personal"].get(key, "") for key in ("name", "email", "phone", "location")}:
        raise ValidationError("生成结果中的个人信息必须与基础简历一致")

    def check_claim(claim: dict[str, Any]) -> None:
        ids = claim.get("source_ids", [])
        if not ids:
            raise ValidationError("生成内容缺少基础简历来源 ID")
        unknown = set(ids) - valid_ids
        if unknown:
            raise ValidationError(f"生成内容引用未知来源 ID: {', '.join(sorted(unknown))}")
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                for nested in value:
                    yield from strings(nested)
            elif isinstance(value, dict):
                for nested in value.values():
                    yield from strings(nested)

        cited_strings = [text.strip() for source_id in ids for text in strings(sources[source_id][1])]
        cited_text = "\n".join(cited_strings)
        if claim.get("content_type") == "confirmed_fact" and claim.get("text", "").strip() not in cited_strings:
            raise ValidationError("标记为已确认事实的内容必须与来源原文一致")
        cited_numbers = set(re.findall(r"\d+(?:[,.]\d+)*(?:%|％)?", cited_text))
        for number in re.findall(r"\d+(?:[,.]\d+)*(?:%|％)?", claim.get("text", "")):
            if number not in raw_numbers or number not in cited_numbers:
                raise ValidationError(f"生成内容包含基础简历中不存在的数字: {number}")

    for claim in generated.get("summary", []):
        check_claim(claim)
    for section in ("education", "work", "projects", "skills", "certificates"):
        for entry in generated.get(section, []):
            source_id = entry.get("source_id")
            if source_id not in valid_ids:
                raise ValidationError(f"生成条目引用未知来源 ID: {source_id}")
            source_section, source_item = sources[source_id]
            expected_section = {"education": "education", "work": "work", "projects": "projects", "skills": "skills", "certificates": "certificates"}[section]
            skill_from_project = section == "skills" and source_section == "projects" and entry.get("title") in source_item.get("technologies", [])
            if source_section != expected_section and not skill_from_project:
                raise ValidationError(f"{section} 条目引用了其他模块的来源 ID: {source_id}")
            if section == "education":
                degree = source_item.get("degree", "")
                major = source_item.get("major", "")
                allowed_titles = {degree, major, f"{major}（{degree}）"}
            elif section == "projects":
                allowed_titles = {source_item.get("name", "")}
            elif section == "skills" and source_section == "projects":
                allowed_titles = set(source_item.get("technologies", []))
            else:
                allowed_titles = {source_item.get("name", "") if section in ("skills", "certificates") else source_item.get("title", source_item.get("name", ""))}
            if entry.get("title") not in allowed_titles:
                raise ValidationError(f"生成的条目标题与来源不一致: {entry.get('title')}")
            if section == "projects":
                expected_org = source_item.get("role", "")
            elif section == "skills":
                expected_org = source_item.get("level", "") if source_section == "skills" else ""
            else:
                expected_org = source_item.get("employer", source_item.get("school", source_item.get("issuer", "")))
            if expected_org and entry.get("organization") != expected_org:
                raise ValidationError(f"生成的机构名称与来源不一致: {entry.get('organization')}")
            if section != "skills":
                for generated_field, source_field in (("start_date", "start_date"), ("end_date", "end_date")):
                    if entry.get(generated_field, "") != source_item.get(source_field, ""):
                        raise ValidationError(f"生成的日期与来源不一致: {generated_field} ({source_id})")
            for claim in entry.get("bullets", []):
                check_claim(claim)
    # A skill or certificate label must be present in a source skill/certificate item.
    for section in ("skills", "certificates"):
        for entry in generated.get(section, []):
            source_section, source_item = sources[entry["source_id"]]
            is_explicit_skill = section == "skills" and source_section == "skills" and entry.get("title") == source_item.get("name")
            is_project_technology = section == "skills" and source_section == "projects" and entry.get("title") in source_item.get("technologies", [])
            is_certificate = section == "certificates" and source_section == "certificates" and entry.get("title") == source_item.get("name")
            if not (is_explicit_skill or is_project_technology or is_certificate):
                raise ValidationError(f"生成的技能或证书没有来源支持: {entry.get('title')}")



def analyze_match(resume: dict[str, Any], requirement: dict[str, Any]) -> dict[str, Any]:
    corpus = all_source_text(resume).casefold()
    required = list(dict.fromkeys(requirement.get("must_haves", []) + requirement.get("preferred", []) + requirement.get("skills", [])))
    matched = [item for item in required if item.casefold() in corpus]
    missing = [item for item in required if item.casefold() not in corpus]
    return {
        "requirement_id": requirement["requirement_id"],
        "matched_requirements": matched,
        "gaps": missing,
        "preferred": requirement.get("preferred", []),
    }


def populate_requirement_gaps(resume: dict[str, Any], requirement: dict[str, Any], generated: dict[str, Any]) -> None:
    """Use deterministic matching terms for gaps instead of model paraphrases."""
    generated["gaps"] = analyze_match(resume, requirement)["gaps"]


def check_requirement_gaps(resume: dict[str, Any], requirement: dict[str, Any], generated: dict[str, Any]) -> None:
    missing = analyze_match(resume, requirement)["gaps"]
    unreported = sorted(set(missing) - set(generated.get("gaps", [])))
    if unreported:
        raise ValidationError("以下简历缺口未标记: " + "、".join(unreported))
    resume_text = "\n".join(
        [claim["text"] for claim in generated.get("summary", [])]
        + [claim["text"] for section in ("education", "work", "projects", "skills", "certificates")
           for entry in generated.get(section, []) for claim in entry.get("bullets", [])]
    ).casefold()
    included = [item for item in missing if item.casefold() in resume_text]
    if included:
        raise ValidationError("能力缺口不能直接写入岗位简历: " + "、".join(included))
