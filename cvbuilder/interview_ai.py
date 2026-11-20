"""One-question-at-a-time interview prompts with explicit fact provenance."""
from __future__ import annotations

import json
import re
from typing import Any

from . import ai


QUESTION_CATEGORIES = ("自我介绍", "简历经历", "行为问题", "职位相关")
MAX_QUESTIONS = 12

REFERENCE_FALLBACKS = {
    "自我介绍": "参考答题框架（请按真实经历填写）：我有[真实的相关经历]，主要负责[真实职责]。在[真实工作或项目]中，我通过[真实行动]取得了[真实结果或收获]。这些经历与目标职位的关联是[据实说明]。",
    "简历经历": "参考答题框架（请按真实经历填写）：在[真实工作或项目]中，我负责[真实职责]。面对[真实问题]，我采取了[真实行动]，最终得到[真实结果或收获]。",
    "行为问题": "参考答题框架（请按真实经历填写）：当时的情况是[真实背景]，我的任务是[真实目标]。我采取了[真实行动]，结果是[真实结果]。事后我学到[真实收获]。",
    "职位相关": "参考答题框架（请按真实经历填写）：我会先确认[问题目标和约束]，再结合[已确认的相关经历]说明[具体做法]，最后用[真实结果或检验方式]评估效果。",
}


def _confirmed_catalog(resume: dict[str, Any]) -> list[str]:
    """Offer the model exact, confirmed source snippets without contact details."""
    snippets: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in {"id", "resume_id", "version", "fixture_note", "email", "phone", "location", "name"}:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, str) and value.strip():
            snippets.append(value.strip())

    collect(resume)
    return list(dict.fromkeys(snippets))


def _confirmed_sections(resume: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {section: [{key: value for key, value in item.items()
                       if key not in {"id", "fixture_note"}}
                      for item in resume.get(section, []) if isinstance(item, dict)]
            for section in ("work", "projects", "education", "skills", "certificates")}


def _unconfirmed_resume_values(value: Any, catalog: list[str]) -> set[str]:
    """Find specific draft values that cannot be repeated as personal facts."""
    if isinstance(value, dict):
        return set().union(*(_unconfirmed_resume_values(child, catalog) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_unconfirmed_resume_values(child, catalog) for child in value))
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 3 and not any(text in fact for fact in catalog):
            return {text}
    return set()


def _call(name: str, instructions: str, payload: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    client, model = ai._client()
    schema = {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }
    response = client.responses.create(
        model=model,
        store=False,
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False),
        text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
    )
    if not response.output_text:
        raise ai.ModelResponseError("模型未返回面试内容")
    try:
        result = json.loads(response.output_text)
    except json.JSONDecodeError as exc:
        raise ai.ModelResponseError("模型面试内容不是有效 JSON") from exc
    if not isinstance(result, dict) or set(result) != set(fields):
        raise ai.ModelResponseError("模型面试内容字段无效")
    for key, spec in fields.items():
        value = result[key]
        if spec["type"] == "string" and (not isinstance(value, str) or len(value) > 4000):
            raise ai.ModelResponseError("模型面试文本无效")
        if spec["type"] == "array" and (not isinstance(value, list) or len(value) > 20 or
                                         any(not isinstance(item, str) or len(item) > 1000 for item in value)):
            raise ai.ModelResponseError("模型面试列表无效")
    return result


def _check_reference_answer(answer: str, session: dict[str, Any], catalog: list[str]) -> str:
    answer = answer.strip()
    if not answer:
        raise ai.ModelResponseError("模型返回了空参考答案")
    unconfirmed = _unconfirmed_resume_values(session.get("resume_content") or {}, catalog)
    if any(value in answer for value in unconfirmed):
        raise ai.ModelResponseError("参考答案引用了未经确认的岗位简历内容")
    confirmed_numbers = {number for fact in catalog for number in re.findall(r"\d+(?:[.,]\d+)*%?", fact)}
    if any(number not in confirmed_numbers for number in re.findall(r"\d+(?:[.,]\d+)*%?", answer)):
        raise ai.ModelResponseError("参考答案包含未经确认的数字")
    return answer


def _confirmed_examples(resume: dict[str, Any]) -> list[tuple[str, str]]:
    """Build short answer examples only from confirmed resume entries."""
    examples: list[tuple[str, str]] = []
    for section in ("work", "projects", "education", "skills", "certificates"):
        for item in resume.get(section) or []:
            if not isinstance(item, dict):
                continue
            values = [value.strip() for key, value in item.items()
                      if key not in {"id", "fixture_note"} and isinstance(value, str) and value.strip()]
            details = [value.strip() for key in ("achievements", "bullets", "details")
                       for value in item.get(key, []) if isinstance(value, str) and value.strip()]
            if section == "work":
                title, employer = item.get("title", ""), item.get("employer", "")
                start = f"我在{employer}担任{title}" if employer and title else (
                    f"我的工作经历包括{employer or title}" if employer or title else "")
            elif section == "projects":
                name, role = item.get("name", ""), item.get("role", "")
                start = f"我参与了{name}" + (f"，担任{role}" if role else "") if name else ""
            elif section == "education":
                school, major = item.get("school", ""), item.get("major", "")
                start = f"我在{school}学习{major}" if school and major else (
                    f"我的教育经历包括{school or major}" if school or major else "")
                if item.get("degree"):
                    start += f"，学历为{item['degree']}"
            elif section == "skills":
                start = f"我的基础简历列有{item.get('name', '')}" if item.get("name") else ""
                if start and item.get("level"):
                    start += f"，熟练程度为{item['level']}"
            else:
                start = f"我的基础简历列有{item.get('name', '')}证书" if item.get("name") else ""
                if start and item.get("issuer"):
                    start += f"，颁发机构为{item['issuer']}"
            if not start:
                continue
            description = item.get("description", "")
            clauses = [start]
            if isinstance(description, str) and description.strip():
                clauses.append(description.strip())
            clauses.extend(details[:2])
            technologies = item.get("technologies") or []
            if section == "projects" and isinstance(technologies, list):
                named = [value.strip() for value in technologies if isinstance(value, str) and value.strip()]
                if named:
                    clauses.append("使用技术包括" + "、".join(named[:3]))
            examples.append(("。".join(clause.rstrip("。") for clause in clauses) + "。",
                             " ".join([*values, *details])))
    return examples


def _specific_fallback(resume: dict[str, Any], category: str, question: str) -> str:
    examples = _confirmed_examples(resume)
    if not examples:
        return REFERENCE_FALLBACKS.get(category, REFERENCE_FALLBACKS["职位相关"])
    question_terms = {question[index:index + 2] for index in range(max(0, len(question) - 1))
                      if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", question[index:index + 2])}
    ranked = sorted(enumerate(examples), key=lambda pair: (
        -sum(term in pair[1][1] for term in question_terms), pair[0]))
    selected = [examples[ranked[0][0]][0]]
    if category == "自我介绍" and len(ranked) > 1:
        selected.append(examples[ranked[1][0]][0])
    note = "" if category == "自我介绍" else "[简历未记录的具体细节，请根据真实经历补充。]"
    return "参考回答（依据已确认的基础简历）：" + "".join(selected) + note


def upgrade_saved_reference(session: dict[str, Any], confirmed_resume: dict[str, Any]) -> str | None:
    """Replace a pending question's older generic framework with confirmed facts."""
    turns = session.get("turns") or []
    if session.get("status") != "awaiting_answer" or not turns or turns[-1].get("answer"):
        return None
    if not str(turns[-1].get("reference_answer") or "").startswith("参考答题框架"):
        return None
    upgraded = _specific_fallback(confirmed_resume, turns[-1].get("category") or "职位相关",
                                  turns[-1].get("question") or "")
    return upgraded if upgraded.startswith("参考回答（依据已确认的基础简历）") else None


def _reference_or_fallback(answer: str, session: dict[str, Any], catalog: list[str],
                           resume: dict[str, Any], category: str, question: str) -> str:
    try:
        checked = _check_reference_answer(answer, session, catalog)
        if not catalog:
            return _specific_fallback(resume, category, question)
        if checked.startswith(("请结合", "请根据", "请据实", "参考答题框架")):
            return _specific_fallback(resume, category, question)
        if any(len(fact) >= 3 for fact in catalog) and not any(
                len(fact) >= 3 and fact in checked for fact in catalog):
            return _specific_fallback(resume, category, question)
        return checked
    except ai.ModelResponseError:
        return _specific_fallback(resume, category, question)


def generate_question(session: dict[str, Any], confirmed_resume: dict[str, Any]) -> dict[str, str]:
    """Generate one question and its pre-answer reference in a single request."""
    turns = session.get("turns") or []
    if len(turns) >= MAX_QUESTIONS:
        raise ValueError("本场面试已达到 12 题上限。")
    category = QUESTION_CATEGORIES[len(turns) % len(QUESTION_CATEGORIES)]
    previous = [{"question": turn.get("question", ""), "answer": turn.get("answer", ""),
                 "follow_up": turn.get("follow_up", "")} for turn in turns]
    catalog = _confirmed_catalog(confirmed_resume)
    payload = {
        "category": category,
        "confirmed_base_resume_facts": catalog,
        "confirmed_base_resume_sections": _confirmed_sections(confirmed_resume),
        "unverified_selected_resume": session.get("resume_content") or {},
        "job_requirements_snapshot": session.get("job_text") or "",
        "prior_turns": previous,
    }
    fields = {"question": {"type": "string"}, "reference_answer": {"type": "string"}}
    result = _call(
        "interview_question",
        "你是中文模拟面试官。每次仅提出一道清晰、具体的问题，类别必须与输入 category 一致。"
        "可根据岗位要求、所选岗位简历草稿和此前回答追问，但它们均不是已确认的个人事实。"
        "已确认个人事实仅来自 confirmed_base_resume_facts；不要把其他输入中的经历、技能、证书或数字称为候选人已确认事实。"
        "不要在问题中断言未经确认的经历。若信息不足，用开放式提问。"
        "同时给出供用户答题前查看的参考答案。根据问题，从 confirmed_base_resume_sections 选取最相关的经历，"
        "写成具体的第一人称示范回答；至少引用一条 confirmed_base_resume_facts 中的原文事实。"
        "参考答案只能使用 confirmed_base_resume_facts 中的个人事实；"
        "不得将岗位简历草稿、招聘要求或先前回答中的未经确认内容写成个人事实。"
        "若缺少作答所需事实，使用方括号占位符提示用户据实补充，不要猜测经历、技能、证书或数字。",
        payload, fields,
    )
    question = result["question"].strip()
    if not question:
        raise ai.ModelResponseError("模型返回了空问题")
    return {"question": question, "category": category,
            "reference_answer": _reference_or_fallback(result["reference_answer"], session, catalog,
                                                       confirmed_resume, category, question)}


def generate_reference_answer(session: dict[str, Any], confirmed_resume: dict[str, Any]) -> str:
    """Backfill the current question from an older session without using its answer."""
    turns = session.get("turns") or []
    if not turns or not isinstance(turns[-1].get("question"), str):
        raise ValueError("当前没有面试问题。")
    catalog = _confirmed_catalog(confirmed_resume)
    result = _call(
        "interview_reference_answer",
        "你是中文模拟面试官。针对给定问题提供一段供用户答题前查看的具体参考答案。"
        "从 confirmed_base_resume_sections 选取最相关的经历，写成第一人称回答，至少引用一条 confirmed_base_resume_facts 中的原文事实。"
        "个人事实只能来自 confirmed_base_resume_facts。岗位要求和岗位简历草稿仅供理解问题，不是已确认事实。"
        "不得使用用户回答；若缺少作答所需事实，使用方括号占位符，不能编造经历、技能、证书或数字。",
        {"question": turns[-1]["question"], "confirmed_base_resume_facts": catalog,
         "confirmed_base_resume_sections": _confirmed_sections(confirmed_resume),
         "job_requirements_snapshot": session.get("job_text") or "",
         "unverified_selected_resume": session.get("resume_content") or {}},
        {"reference_answer": {"type": "string"}},
    )
    return _reference_or_fallback(result["reference_answer"], session, catalog, confirmed_resume,
                                  turns[-1].get("category") or "职位相关", turns[-1]["question"])


def generate_feedback(session: dict[str, Any], confirmed_resume: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the latest answer while separating fact sources in the output."""
    turns = session.get("turns") or []
    if not turns or not isinstance(turns[-1].get("answer"), str) or not turns[-1]["answer"].strip():
        raise ValueError("尚未提交文字回答。")
    catalog = _confirmed_catalog(confirmed_resume)
    payload = {
        "confirmed_base_resume_facts": catalog,
        "unverified_selected_resume": session.get("resume_content") or {},
        "job_requirements_snapshot": session.get("job_text") or "",
        "question": turns[-1].get("question") or "",
        "user_answer_self_report": turns[-1]["answer"],
        "prior_turns": [{"question": turn.get("question", ""), "answer": turn.get("answer", "")}
                        for turn in turns[:-1]],
    }
    fields = {
        "evaluation": {"type": "string"},
        "suggestions": {"type": "string"},
        "answer_points": {"type": "string"},
        "follow_up": {"type": "string"},
        "confirmed_facts": {"type": "array", "items": {"type": "string"}},
        "user_claims": {"type": "array", "items": {"type": "string"}},
        "unconfirmed_suggestions": {"type": "array", "items": {"type": "string"}},
    }
    result = _call(
        "interview_feedback",
        "你是中文模拟面试官。评价本题回答，给出可操作的改进建议和回答要点，可给出一道追问。"
        "只把 confirmed_base_resume_facts 中逐字存在的条目放进 confirmed_facts；其他来源均不得标成已确认事实。"
        "用户回答中的个人叙述放进 user_claims，并明确其为用户自述、未经基础简历确认。"
        "岗位简历草稿和招聘要求中的能力、经历、证书及成果若没有基础简历依据，只能作为待确认建议。"
        "evaluation、suggestions、answer_points 和 follow_up 不得将这些未经确认的内容写成事实；"
        "回答要点以通用结构和表达建议为主。不要编造个人经历、技能、证书或数字。",
        payload, fields,
    )
    if not result["evaluation"].strip() or not result["suggestions"].strip() or not result["answer_points"].strip():
        raise ai.ModelResponseError("模型反馈内容不完整")
    if any(fact not in catalog for fact in result["confirmed_facts"]):
        raise ai.ModelResponseError("模型把未经确认内容标成了事实")
    return result
