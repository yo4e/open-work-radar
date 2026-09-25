import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scripts import fetch_github


class Issue14Tests(unittest.TestCase):
    NOW = dt.datetime(2026, 9, 17, 0, 0, tzinfo=dt.timezone.utc)

    def issue(
        self,
        number,
        *,
        project="example/project",
        state="open",
        title="Bounty: $25",
        body="Bounty: $25 for this work.",
        labels=None,
        assignees=None,
        author_association="OWNER",
        updated_at="2026-09-16T00:00:00Z",
    ):
        return {
            "number": number,
            "state": state,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/{project}/issues/{number}",
            "repository_url": f"https://api.github.com/repos/{project}",
            "labels": [{"name": label} for label in (labels or [])],
            "assignees": [{"login": login} for login in (assignees or [])],
            "author_association": author_association,
            "created_at": "2026-09-01T00:00:00Z",
            "updated_at": updated_at,
        }

    def normalize(self, item, now=None):
        return fetch_github.normalize_issue(
            item,
            "one",
            fetch_github.iso_z(now or self.NOW),
            now or self.NOW,
            180,
            set(),
        )

    def sources(self, directory, max_pages=2):
        path = Path(directory) / "sources.yaml"
        path.write_text(
            "stale_after_days: 180\nexclude_repositories: []\nsources:\n"
            f"  - id: one\n    query: one\n    max_pages: {max_pages}\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def pr_event(number, *, state="open", draft=False, text=None, project="example/project", pr=41):
        return {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "state": state,
                    "draft": draft,
                    "title": "Implementation",
                    "body": text or f"Fixes #{number}",
                    "html_url": f"https://github.com/{project}/pull/{pr}",
                    "pull_request": {"url": f"https://api.github.com/repos/{project}/pulls/{pr}"},
                }
            },
        }

    def test_hhs_pr_submitted_with_actual_open_pr_is_excluded(self):
        item = self.issue(
            9814,
            project="HHS/simpler-grants-gov",
            title="Bounty: $50",
            body="Bounty: $50. PR submitted.",
        )
        record = self.normalize(item)
        signals = {"comments": [], "timeline": [self.pr_event(9814, project="HHS/simpler-grants-gov", pr=12178)]}
        self.assertIsNotNone(record)
        self.assertIsNone(fetch_github.second_stage(record, item, signals, self.NOW))

    def test_pr_submitted_body_without_actual_pr_is_only_a_hint(self):
        item = self.issue(9814, body="Bounty: $25. PR submitted.")
        record = self.normalize(item)
        kept = fetch_github.second_stage(record, item, {"comments": [], "timeline": []}, self.NOW)
        self.assertIsNotNone(kept)

    def test_ultimate_open_and_draft_prs_are_excluded(self):
        for number, pr, draft in ((14, 41, False), (9, 42, True)):
            with self.subTest(number=number):
                item = self.issue(number, project="iyeanur6-cyber/ultimate-ai-platform")
                record = self.normalize(item)
                signals = {
                    "comments": [],
                    "timeline": [
                        self.pr_event(
                            number,
                            project="iyeanur6-cyber/ultimate-ai-platform",
                            pr=pr,
                            draft=draft,
                        )
                    ],
                }
                self.assertIsNone(fetch_github.second_stage(record, item, signals, self.NOW))

    def test_closed_or_irrelevant_pr_does_not_suppress_issue(self):
        item = self.issue(14)
        record = self.normalize(item)
        closed = self.pr_event(14, state="closed")
        irrelevant = self.pr_event(99, text="Fixes #99", pr=42)
        kept = fetch_github.second_stage(
            record,
            item,
            {"comments": [], "timeline": [closed, irrelevant]},
            self.NOW,
        )
        self.assertIsNotNone(kept)

    def test_plain_comment_with_fixes_keyword_does_not_count_as_pr(self):
        item = self.issue(14)
        record = self.normalize(item)
        signals = {
            "comments": [
                {
                    "body": "Please open a PR with `Fixes #14` in the description.",
                    "author_association": "MEMBER",
                }
            ],
            "timeline": [],
        }
        kept = fetch_github.second_stage(record, item, signals, self.NOW)
        self.assertIsNotNone(kept)

    def test_memanto_expired_explicit_utc_deadline_is_excluded(self):
        item = self.issue(
            1609,
            project="moorcheh-ai/memanto",
            body="Bounty: $40\nsubmission deadline of 2026-09-15 23:59 UTC",
        )
        record = self.normalize(item)
        self.assertEqual(record["deadline"], "2026-09-15T23:59:00Z")
        self.assertIsNone(fetch_github.second_stage(record, item, {}, self.NOW))

    def test_deadline_without_understood_explicit_timezone_is_left_unknown(self):
        self.assertIsNone(fetch_github.parse_deadline("deadline 2026-09-15 23:59"))
        self.assertIsNone(fetch_github.parse_deadline("deadline 2026-09-15 23:59 MARS"))
        self.assertEqual(
            fetch_github.parse_deadline("deadline 2026-09-15 23:59 JST"),
            dt.datetime(2026, 9, 15, 14, 59, tzinfo=dt.timezone.utc),
        )

    def test_ambiguous_bounty_is_excluded_until_newer_maintainer_confirmation(self):
        item = self.issue(
            34,
            project="cxlinux-ai/cx-distro",
            body="Bounty: $25 upon merge.",
        )
        record = self.normalize(item)
        questions = {
            "comments": [
                {"body": "is this bounty still active?", "author_association": "NONE"},
                {"body": "is the bounty still funded?", "author_association": "CONTRIBUTOR"},
            ],
            "timeline": [],
        }
        self.assertIsNone(fetch_github.second_stage(record, item, questions, self.NOW))

        confirmed = {
            "comments": [
                *questions["comments"],
                {"body": "The bounty is still active.", "author_association": "MEMBER"},
            ],
            "timeline": [],
        }
        self.assertIsNotNone(fetch_github.second_stage(record, item, confirmed, self.NOW))

    def test_multi_claim_bounty_is_not_excluded_without_actual_pr(self):
        item = self.issue(
            2155,
            project="Scottcjn/rustchain-bounties",
            title="Bounty: $100",
            body="Bounty: $100 for the parser fix.",
        )
        record = self.normalize(item)
        signals = {
            "comments": [
                {"body": "/attempt #2155", "author_association": "NONE"},
                {"body": "/claim", "author_association": "NONE"},
                {"body": "/try", "author_association": "CONTRIBUTOR"},
            ],
            "timeline": [],
        }
        self.assertIsNotNone(fetch_github.second_stage(record, item, signals, self.NOW))

    def test_funding_pending_label_is_not_actionable(self):
        item = self.issue(
            17,
            project="0xSachinK/openplaid",
            title="[$200 bounty · funding pending] Banco integration",
            body=(
                "$200 USD planned bounty — NOT FUNDED. No funds are deposited/escrowed today. "
                "Do not start paid work in reliance on this listing."
            ),
            labels=["$200", "funding-pending", "integration"],
        )
        self.assertIsNone(self.normalize(item))

    def test_search_window_miss_is_directly_revalidated_and_preserved(self):
        shape = self.issue(3, project="Henry00IS/ShapeEditor", title="Bounty: $25")
        other = self.issue(8, project="example/other", title="Bounty: $40")

        class WindowClient:
            def __init__(self):
                self.run = 0

            def search_issues(self, query, max_pages=1):
                self.run += 1
                return iter([shape] if self.run == 1 else [other])

            def lifecycle_signals(self, project, number):
                return {"comments": [], "timeline": []}

            def fetch_issue(self, project, number):
                if (project, number) == ("Henry00IS/ShapeEditor", 3):
                    return shape
                return None

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "opportunities.json"
            client = WindowClient()
            fetch_github.collect(self.sources(directory), output, client, now=self.NOW)
            later = self.NOW + dt.timedelta(days=1)
            fetch_github.collect(self.sources(directory), output, client, now=later)
            rows = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual({row["issue_number"] for row in rows}, {3, 8})

    def test_missing_record_that_closed_is_removed_not_preserved_forever(self):
        open_item = self.issue(3, project="Henry00IS/ShapeEditor")
        closed_item = self.issue(3, project="Henry00IS/ShapeEditor", state="closed")

        class ClosingClient:
            def __init__(self):
                self.run = 0

            def search_issues(self, query, max_pages=1):
                self.run += 1
                return iter([open_item] if self.run == 1 else [])

            def lifecycle_signals(self, project, number):
                return {"comments": [], "timeline": []}

            def fetch_issue(self, project, number):
                return closed_item

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "opportunities.json"
            client = ClosingClient()
            fetch_github.collect(self.sources(directory), output, client, now=self.NOW)
            fetch_github.collect(
                self.sources(directory),
                output,
                client,
                now=self.NOW + dt.timedelta(days=1),
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), [])

    def test_direct_revalidation_failure_preserves_prior_record(self):
        item = self.issue(3, project="Henry00IS/ShapeEditor")

        class FlakyClient:
            def __init__(self):
                self.run = 0

            def search_issues(self, query, max_pages=1):
                self.run += 1
                return iter([item] if self.run == 1 else [])

            def lifecycle_signals(self, project, number):
                return {"comments": [], "timeline": []}

            def fetch_issue(self, project, number):
                raise fetch_github.DirectValidationUnavailable("timeout")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "opportunities.json"
            client = FlakyClient()
            fetch_github.collect(self.sources(directory), output, client, now=self.NOW)
            before = output.read_text(encoding="utf-8")
            fetch_github.collect(
                self.sources(directory),
                output,
                client,
                now=self.NOW + dt.timedelta(days=1),
            )
            self.assertEqual(output.read_text(encoding="utf-8"), before)

    def test_timestamp_only_rescan_is_not_commit_worthy_but_status_is_fresh(self):
        item = self.issue(3, project="Henry00IS/ShapeEditor")

        class StableClient:
            def search_issues(self, query, max_pages=1):
                return iter([item])

            def lifecycle_signals(self, project, number):
                return {"comments": [], "timeline": []}

            def fetch_issue(self, project, number):
                return item

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "opportunities.json"
            status = Path(directory) / "status.json"
            client = StableClient()
            fetch_github.collect(self.sources(directory), output, client, now=self.NOW, status_path=status)
            first_bytes = output.read_bytes()
            first_row = json.loads(output.read_text(encoding="utf-8"))[0]
            later = self.NOW + dt.timedelta(days=1)
            fetch_github.collect(self.sources(directory), output, client, now=later, status_path=status)
            second_status = json.loads(status.read_text(encoding="utf-8"))
            second_row = json.loads(output.read_text(encoding="utf-8"))[0]

            self.assertEqual(output.read_bytes(), first_bytes)
            self.assertFalse(second_status["substantive_changed"])
            self.assertEqual(second_status["checked_at"], fetch_github.iso_z(later))
            self.assertEqual(second_row["last_checked_at"], first_row["last_checked_at"])
            self.assertEqual(second_row["last_changed_at"], first_row["last_changed_at"])

    def test_configured_two_page_search_is_used(self):
        seen = []

        class TrackingClient:
            def search_issues(self, query, max_pages=1):
                seen.append(max_pages)
                return iter([])

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "opportunities.json"
            fetch_github.collect(self.sources(directory, max_pages=2), output, TrackingClient(), now=self.NOW)
        self.assertEqual(seen, [2])


if __name__ == "__main__":
    unittest.main()
