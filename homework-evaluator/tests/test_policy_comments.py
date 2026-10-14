"""Policy, fair scoring and publication boundaries without external writes."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_review import review_fixture

from ax_evaluator.comments import publish, render_comment, write_comment
from ax_evaluator.policy import interview
from ax_evaluator.review import build_bundle, score, validate_review
from ax_evaluator.storage import digest


def complete_fixture():
    snapshot, bundle, review = review_fixture()
    for item in review["items"]:
        item.update(
            status="pass",
            reason="Core requirement supported",
            evidence=[{"path": "REPORT.md", "start": 1, "end": 1}],
            feedback_type="strength",
            feedback="기록을 잘 남겼습니다.",
            pending_reason="",
            rerun="not-needed",
        )
    review["bonus_review_complete"] = True
    return snapshot, bundle, review


def bonus(criterion="", key="extra"):
    return dict(
        id="B1",
        title="Extra",
        status="pass",
        contribution="Beyond requirements",
        validation="Recorded evidence",
        limitations="Small sample",
        evidence=[{"path": "REPORT.md", "start": 1, "end": 1}],
        feedback_type="strength",
        feedback="추가 검증이 있습니다.",
        severity="none",
        rerun="not-needed",
        criterion_id=criterion,
        contribution_key=key,
    )


class PolicyTest(unittest.TestCase):
    def test_interview_without_explicit_rules_keeps_meaningful_extras(self):
        answers = iter(["n", ""])
        policy = interview(lambda _: next(answers))
        self.assertEqual(policy["explicit_criteria"], [])
        self.assertTrue(policy["open_ended"])
        self.assertIsNone(policy["cap"])

    def test_explicit_interview_records_rule_points_and_cap(self):
        answers = iter(
            ["y", "Robustness", "Independent failure tests with logs", "2", "", "4"]
        )
        policy = interview(lambda _: next(answers))
        self.assertEqual(policy["explicit_criteria"][0]["points"], 2)
        self.assertEqual(policy["cap"], 4)
        self.assertTrue(policy["open_ended"])

    def test_minor_defect_cannot_deduct_but_pass_can_request_revision(self):
        snapshot, bundle, review = complete_fixture()
        item = review["items"][0]
        item.update(
            severity="minor", feedback_type="revision", feedback="표현을 수정하세요."
        )
        validate_review(review, bundle, snapshot)
        self.assertEqual(score(bundle, review)["base_earned"], 20)
        item.update(status="fail", deduction_reason="A typo")
        with self.assertRaisesRegex(ValueError, "material defect"):
            validate_review(review, bundle, snapshot)

    def test_explicit_points_and_semantic_duplicate_guard(self):
        snapshot, bundle, review = complete_fixture()
        rubric = copy.deepcopy(bundle["rubric"])
        rubric["bonus_policy"]["explicit_criteria"] = [
            dict(id="E1", title="Robustness", rule="Independent tests", points=2)
        ]
        bundle = build_bundle(snapshot, rubric)
        review["bonuses"] = [bonus("E1")]
        validate_review(review, bundle, snapshot)
        self.assertEqual(score(bundle, review)["bonus_earned"], 2)
        review["bonuses"].append({**bonus(), "id": "B2", "title": "Different name"})
        with self.assertRaisesRegex(ValueError, "twice"):
            validate_review(review, bundle, snapshot)
        review["bonuses"] = []
        with self.assertRaisesRegex(ValueError, "Every explicit"):
            validate_review(review, bundle, snapshot)

    def test_other_assignment_review_only_has_no_week05_checks(self):
        snapshot, bundle, _ = complete_fixture()
        rubric = copy.deepcopy(bundle["rubric"])
        rubric.update(week="week-06", checker="review-only")
        rubric["items"] = [{**rubric["items"][0], "id": "R1", "mode": "review"}]
        other = build_bundle(snapshot, rubric)
        self.assertEqual(list(other["checks"]["items"]), ["R1"])
        self.assertEqual(other["checks"]["aggregates"], {})


class CommentsTest(unittest.TestCase):
    def test_material_revision_survives_pass_minor_stays_in_detail(self):
        snapshot, bundle, review = complete_fixture()
        review["items"][0].update(
            feedback_type="revision",
            severity="material",
            feedback="주요 해석을 수정하세요.",
        )
        review["items"][1].update(
            feedback_type="revision", severity="minor", feedback="typo-only"
        )
        comment = render_comment(score(bundle, review), bundle, snapshot)
        self.assertIn("주요 해석", comment)
        self.assertNotIn("typo-only", comment)
        self.assertIn("새 모델 실험 없이", comment)
        self.assertNotIn("20점", comment)

    def test_policy_question_is_not_student_revision(self):
        snapshot, bundle, review = complete_fixture()
        review["items"][0].update(
            status="pending",
            pending_reason="policy",
            feedback_type="confirmation",
            feedback="교수자 정책 질문",
        )
        comment = render_comment(score(bundle, review), bundle, snapshot)
        self.assertIn("교수자 확인 사항", comment)
        self.assertNotIn("교수자 정책 질문", comment)

    def make_final(self, root, incomplete=False):
        snapshot, bundle, review = complete_fixture()
        snapshot["metadata"].update(
            source="github",
            repository="example/course",
            pr=7,
            head_sha="abc123",
            student_id="123",
            week="week-05",
        )
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        bundle = build_bundle(snapshot, bundle["rubric"])
        report = score(bundle, None if incomplete else review)
        write_comment(root, report, bundle, snapshot)

    def test_preview_is_offline_and_incomplete_or_modified_drafts_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_final(root)
            with patch("ax_evaluator.comments.github") as api:
                self.assertEqual(publish(root)["action"], "preview-only")
                api.assert_not_called()
            (root / "pr-comment.md").write_text("changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                publish(root, write=True)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_final(root, incomplete=True)
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                publish(root, write=True)

    def test_stale_pr_blocks_write_and_existing_own_comment_is_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_final(root)
            with patch(
                "ax_evaluator.comments.github",
                return_value={"head": {"sha": "different"}, "state": "open"},
            ) as api:
                with self.assertRaisesRegex(ValueError, "head changed"):
                    publish(root, write=True)
                self.assertEqual(api.call_count, 1)
            marker = "<!-- ax-homework-evaluator:example/course:7:week-05 -->"
            responses = [
                {"head": {"sha": "abc123"}, "state": "open"},
                {"login": "grader"},
                [
                    {"id": 4, "user": {"login": "grader"}, "body": marker + "\nold"},
                    {"id": 5, "user": {"login": "student"}, "body": marker + "\nother"},
                ],
                {"head": {"sha": "abc123"}, "state": "open"},
                {"html_url": "https://example.invalid/comment/4"},
            ]
            with patch("ax_evaluator.comments.github", side_effect=responses) as api:
                self.assertEqual(publish(root, write=True)["action"], "updated")
                args = api.call_args.args
                self.assertEqual(
                    args[:2], ("repos/example/course/issues/comments/4", "PATCH")
                )


if __name__ == "__main__":
    unittest.main()
