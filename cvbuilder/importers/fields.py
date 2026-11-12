"""Conservative local extraction of contact fields and explicit skills."""
from __future__ import annotations

import re
from typing import Any

EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
PHONE_LABEL = re.compile(r"(?:手机号码|联系电话|手机|电话|Mobile|Phone|Tel)[ \t]*[:：]?[ \t]*(\+?\d[\d ()-]{5,20}\d)", re.I)
CHINA_MOBILE = re.compile(r"(?<!\d)(?:\+?86[ -]?)?1[3-9]\d{9}(?!\d)")
NZ_MOBILE = re.compile(r"(?<!\d)(?:\+64[ -]?|0)2\d(?:[ -]?\d){7,9}(?!\d)")
NAME_LABEL = re.compile(r"(?im)^[ \t]*(?:姓名|Name)[ \t]*[:：][ \t]*([^\n|｜,，;；]{2,40})")
SKILL_HEADING = re.compile(r"^(?:专业技能|技能清单|技能[ /／、]+技术栈|技能|技术栈|Technical Skills|Skills)[ \t]*[:：]?[ \t]*(.*)$", re.I)
OTHER_HEADING = re.compile(r"^(?:个人信息|教育经历|教育背景|工作经历|工作经验|项目经历|项目经验|证书|自我评价|求职意向|Personal|Education|Experience|Projects|Certificates)[ \t]*[:：]?$", re.I)


def extract_personal(text: str) -> dict[str, str]:
    result = {"name": "", "email": "", "phone": "", "location": ""}
    email = EMAIL.search(text)
    if email:
        result["email"] = email.group()
    for pattern in (PHONE_LABEL, CHINA_MOBILE, NZ_MOBILE):
        for match in pattern.finditer(text):
            value = match.group(1) if pattern is PHONE_LABEL else match.group()
            if 7 <= len(re.sub(r"\D", "", value)) <= 15:
                result["phone"] = value.strip()
                break
        if result["phone"]:
            break
    labeled = NAME_LABEL.search(text)
    if labeled:
        candidate = labeled.group(1).strip()
        if re.fullmatch(r"[\u4e00-\u9fff·]{2,6}|[A-Za-z][A-Za-z .'-]{1,39}", candidate):
            result["name"] = candidate
    if not result["name"]:
        for line in text.splitlines()[:6]:
            candidate = line.strip()
            if re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", candidate) and not any(word in candidate for word in ("简历", "信息", "教育", "工作", "项目", "技能", "证书")):
                result["name"] = candidate
                break
    return result


def redact_personal(text: str, personal: dict[str, str]) -> str:
    text = EMAIL.sub("[邮箱已在本机提取]", text)
    for key in ("phone", "name"):
        value = personal.get(key, "")
        if value:
            text = text.replace(value, f"[{key}已在本机提取]")
    return text


def explicit_skill_names(text: str) -> set[str]:
    lines = text.splitlines()
    content: list[str] = []
    in_section = False
    for line in lines:
        line = line.strip()
        heading = SKILL_HEADING.fullmatch(line)
        if heading:
            in_section = True
            if heading.group(1):
                content.append(heading.group(1))
            continue
        if in_section and OTHER_HEADING.fullmatch(line):
            break
        if in_section and line:
            content.append(line)
    names = set()
    for line in content:
        for part in re.split(r"[、，,；;|｜\n]+", line):
            part = re.sub(r"^[\s•·*\-\d.]+", "", part).strip()
            part = re.split(r"[：:]", part, maxsplit=1)[0].strip()
            part = re.sub(r"[（(](?:熟练|精通|了解|掌握|基础)[^）)]*[）)]$", "", part).strip()
            if part:
                names.add(part.casefold())
    return names


def keep_explicit_skills(draft: dict[str, Any], text: str) -> dict[str, Any]:
    allowed = explicit_skill_names(text)
    seen: set[str] = set()
    retained = []
    for item in draft.get("skills", []):
        name = str(item.get("name", "")).strip()
        key = name.casefold()
        if key in allowed and key not in seen:
            item["name"] = name
            retained.append(item)
            seen.add(key)
    draft["skills"] = retained
    return draft
