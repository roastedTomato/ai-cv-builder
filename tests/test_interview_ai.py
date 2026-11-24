"""Interview AI tests use only fictional test data and mocked model output."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from cvbuilder import ai, interview_ai


class InterviewAITests(unittest.TestCase):
    def setUp(self):
        self.resume = {"personal": {"name": "虚构姓名", "email": "fictional@example.invalid"},
                       "work": [{"id": "test-work", "employer": "虚构甲公司", "title": "测试工程师",
                                 "bullets": ["维护测试用例"]}]}
        self.session = {"job_text": "要求掌握量子计算（虚构测试招聘要求）",
                        "resume_content": {"skills": [{"title": "量子计算"}]},
                        "turns": [{"question": "请介绍项目", "answer": "我会量子计算（虚构用户自述）"}]}

    def test_feedback_requests_only_brief_evaluation_and_answer_points(self):
        captured = {}

        def fake_call(name, instructions, payload, fields):
            captured.update({"name": name, "instructions": instructions, "payload": payload, "fields": fields})
            return {"evaluation": "回答有条理。", "answer_points": "背景、行动、结果。"}

        with patch("cvbuilder.interview_ai._call", side_effect=fake_call):
            feedback = interview_ai.generate_feedback(self.session, self.resume)
        self.assertEqual(captured["name"], "interview_feedback")
        self.assertIn("维护测试用例", captured["payload"]["confirmed_base_resume_facts"])
        self.assertNotIn("量子计算", captured["payload"]["confirmed_base_resume_facts"])
        self.assertEqual(captured["payload"]["user_answer_self_report"], self.session["turns"][-1]["answer"])
        self.assertEqual(set(captured["fields"]), {"evaluation", "answer_points"})
        self.assertIn("一两句简短评价", captured["instructions"])
        self.assertIn("不得将其中未经确认的经历", captured["instructions"])
        self.assertEqual(feedback, {"evaluation": "回答有条理。", "answer_points": "背景、行动、结果。"})

    def test_missing_answer_points_are_rejected(self):
        output = {"evaluation": "回答有条理", "answer_points": ""}
        with patch("cvbuilder.interview_ai._call", return_value=output):
            with self.assertRaises(ai.ModelResponseError):
                interview_ai.generate_feedback(self.session, self.resume)

    def test_empty_reference_answer_uses_safe_fallback_without_losing_question(self):
        output = {"question": "请介绍项目", "reference_answer": ""}
        with patch("cvbuilder.interview_ai._call", return_value=output):
            generated = interview_ai.generate_question(self.session, self.resume)
        self.assertEqual(generated["question"], "请介绍项目")
        self.assertIn("虚构甲公司", generated["reference_answer"])
        self.assertIn("维护测试用例", generated["reference_answer"])

    def test_unsafe_reference_answer_uses_fallback_without_unconfirmed_facts(self):
        output = {"question": "请介绍项目", "reference_answer": "我掌握量子计算。"}
        with patch("cvbuilder.interview_ai._call", return_value=output):
            generated = interview_ai.generate_question(self.session, self.resume)
        self.assertEqual(generated["question"], "请介绍项目")
        self.assertNotIn("量子计算", generated["reference_answer"])
        output["reference_answer"] = "我把结果提高了 50%。"
        with patch("cvbuilder.interview_ai._call", return_value=output):
            generated = interview_ai.generate_question(self.session, self.resume)
        self.assertNotIn("50%", generated["reference_answer"])
        self.assertIn("维护测试用例", generated["reference_answer"])

    def test_question_includes_reference_before_user_answer(self):
        session = {**self.session, "turns": []}
        captured = {}
        def fake_call(name, instructions, payload, fields):
            captured.update({"name": name, "instructions": instructions, "payload": payload, "fields": fields})
            return {"question": "请介绍测试经历", "reference_answer": "我曾维护测试用例。"}
        with patch("cvbuilder.interview_ai._call", side_effect=fake_call):
            question = interview_ai.generate_question(session, self.resume)
        self.assertEqual(question["reference_answer"], "我曾维护测试用例。")
        self.assertIn("供用户答题前查看", captured["instructions"])
        self.assertNotIn("user_answer_self_report", captured["payload"])
        self.assertIn("reference_answer", captured["fields"])
        self.assertEqual(captured["payload"]["confirmed_base_resume_sections"]["work"][0]["employer"], "虚构甲公司")

    def test_generic_reference_is_replaced_with_relevant_confirmed_project(self):
        resume = {"projects": [{"name": "虚构课程预约项目", "role": "开发成员",
                               "description": "开发预约原型。", "achievements": ["实现预约记录查询。"]}],
                  "work": [{"employer": "虚构文档公司", "title": "文档助理",
                            "description": "整理内部文档。"}]}
        session = {"job_text": "虚构招聘要求", "resume_content": {},
                   "turns": [{"question": "旧问题", "answer": "旧回答"}]}
        with patch("cvbuilder.interview_ai._call", return_value={
                "question": "请介绍虚构课程预约项目", "reference_answer": "请结合真实经历回答。"}):
            generated = interview_ai.generate_question(session, resume)
        self.assertIn("虚构课程预约项目", generated["reference_answer"])
        self.assertIn("实现预约记录查询", generated["reference_answer"])
        self.assertNotIn("虚构文档公司", generated["reference_answer"])

    def test_instruction_style_reference_with_a_fact_is_still_personalized(self):
        session = {**self.session, "turns": []}
        with patch("cvbuilder.interview_ai._call", return_value={
                "question": "请介绍工作经历", "reference_answer": "请结合维护测试用例的真实经历回答。"}):
            generated = interview_ai.generate_question(session, self.resume)
        self.assertIn("虚构甲公司", generated["reference_answer"])
        self.assertFalse(generated["reference_answer"].startswith("请结合"))

    def test_no_confirmed_resume_facts_keeps_explicit_fill_in_framework(self):
        session = {"job_text": "虚构招聘要求", "resume_content": {}, "turns": []}
        with patch("cvbuilder.interview_ai._call", return_value={
                "question": "请介绍工作经历", "reference_answer": "请结合真实经历回答。"}):
            generated = interview_ai.generate_question(session, {})
        self.assertIn("参考答题框架", generated["reference_answer"])
        self.assertIn("[真实", generated["reference_answer"])

    def test_backfill_older_question_ignores_user_answer(self):
        session = {**self.session, "turns": [{"question": "请介绍项目", "answer": None}]}
        with patch("cvbuilder.interview_ai._call", return_value={"reference_answer": "请依据已确认经历举例。"}) as call:
            answer = interview_ai.generate_reference_answer(session, self.resume)
        self.assertIn("虚构甲公司", answer)
        self.assertIn("维护测试用例", answer)
        self.assertNotIn("user_answer_self_report", call.call_args.args[2])

    def test_question_categories_cover_required_types(self):
        for index, category in enumerate(interview_ai.QUESTION_CATEGORIES):
            session = {"job_text": "虚构招聘要求", "resume_content": {},
                       "turns": [{"question": "虚构旧问题", "answer": "虚构旧回答"}] * index}
            with self.subTest(category=category), patch("cvbuilder.interview_ai._call", return_value={"question": "请回答测试问题", "reference_answer": "请根据已确认经历作答。"}):
                self.assertEqual(interview_ai.generate_question(session, self.resume)["category"], category)

    def test_generates_twelve_questions_and_safe_references_in_one_request(self):
        captured = []
        questions = [{"category": interview_ai.QUESTION_CATEGORIES[i % 4],
                      "question": f"虚构测试问题 {i + 1}？",
                      "reference_answer": "我掌握量子计算。" if i == 0 else "我维护测试用例。"}
                     for i in range(12)]
        def fake_call(name, instructions, payload, fields):
            captured.append((name, instructions, payload, fields))
            return {"questions": questions}
        with patch("cvbuilder.interview_ai._call", side_effect=fake_call):
            result = interview_ai.generate_questions(self.session, self.resume)
        self.assertEqual(len(captured), 1)
        self.assertEqual(len(result), 12)
        self.assertIn("通顺", captured[0][1])
        self.assertNotIn("量子计算", result[0]["reference_answer"])
        self.assertIn("维护测试用例", result[0]["reference_answer"])

    def test_rejects_incomplete_or_duplicate_question_set(self):
        questions = [{"category": interview_ai.QUESTION_CATEGORIES[i % 4],
                      "question": f"虚构测试问题 {i + 1}？", "reference_answer": "我维护测试用例。"}
                     for i in range(12)]
        with patch("cvbuilder.interview_ai._call", return_value={"questions": questions[:-1]}):
            with self.assertRaises(ai.ModelResponseError):
                interview_ai.generate_questions(self.session, self.resume)
        questions[1]["question"] = questions[0]["question"]
        with patch("cvbuilder.interview_ai._call", return_value={"questions": questions}):
            with self.assertRaises(ai.ModelResponseError):
                interview_ai.generate_questions(self.session, self.resume)


if __name__ == "__main__":
    unittest.main()
