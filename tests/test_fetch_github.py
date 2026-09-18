import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import fetch_github


class FakeClient:
    def __init__(self, responses):
        self.responses = responses

    def search_issues(self, query, max_pages=1):
        response = self.responses[query]
        if isinstance(response, Exception):
            raise response
        return iter(response)


class CollectorTests(unittest.TestCase):
    NOW = dt.datetime(2026, 9, 9, 1, 0, tzinfo=dt.timezone.utc)

    def issue(self, number, *, state="open", title="Docs bounty: $25", body="Please update the README.", labels=None, assignees=None, author_association="OWNER", project="example/project", updated_at="2026-09-08T00:00:00Z"):
        return {
            "number": number, "state": state, "title": title, "body": body,
            "html_url": f"https://github.com/{project}/issues/{number}",
            "repository_url": f"https://api.github.com/repos/{project}",
            "labels": [{"name": label} for label in (labels or [])],
            "assignees": [{"login": login} for login in (assignees or [])],
            "author_association": author_association, "created_at": "2026-09-01T00:00:00Z", "updated_at": updated_at,
        }

    def sources(self, directory):
        path = Path(directory) / "sources.yaml"
        path.write_text(
            "stale_after_days: 180\nexclude_repositories:\n  - yo4e/open-work-radar\nsources:\n"
            "  - id: one\n    query: one\n"
            "  - id: two\n    query: two\n", encoding="utf-8"
        )
        return path

    def test_closed_are_excluded_and_overlaps_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data.json"
            shared = self.issue(1)
            client = FakeClient({"one": [shared, self.issue(2, state="closed")], "two": [shared]})
            count, successful, failed = fetch_github.collect(self.sources(directory), output, client, now=self.NOW)
            records = json.loads(output.read_text())
            self.assertEqual((count, successful, failed), (1, 2, []))
            self.assertEqual(records[0]["discovery_sources"], ["one", "two"])

    def test_maintainer_reward_is_stated(self):
        reward = fetch_github.reward_metadata("Bounty: $50", "", [], "OWNER")
        self.assertEqual((reward["amount"], reward["currency"], reward["provenance"]), (50, "USD", "stated"))
        self.assertFalse(reward["verified"])

    def test_outsider_reward_is_not_stated(self):
        reward = fetch_github.reward_metadata("Bounty: $50", "", [], "NONE")
        self.assertEqual(reward["amount"], 50)
        self.assertEqual(reward["provenance"], "unverified")

    def test_outsider_direct_reward_is_not_actionable_without_maintainer_evidence(self):
        item = self.issue(20, title="Bounty: $50", body="Please fix the parser.", author_association="NONE")
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "NONE")
        self.assertEqual(fetch_github.candidate_classification(item["title"], item["body"], [], "NONE", reward), "third_party_claim")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_contributor_reward_proposal_is_excluded(self):
        item = self.issue(
            21,
            title="Proposed $25 docs bounty",
            body="Would you approve a $25 bounty for this documentation change?",
            author_association="CONTRIBUTOR",
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "CONTRIBUTOR")
        self.assertEqual(fetch_github.candidate_classification(item["title"], item["body"], [], "CONTRIBUTOR", reward), "contributor_proposal")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_external_mirror_is_excluded_even_when_owner_authored(self):
        item = self.issue(
            22,
            title="[other/repo] [Bounty $50] Fix parser",
            body="### 赏金平台 / Platform GitHub\n### 原始链接 / Source URL\nhttps://github.com/other/repo/issues/7",
            labels=["bounty", "external-mirror"],
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], item["labels"] and ["bounty", "external-mirror"], "OWNER")
        self.assertEqual(fetch_github.candidate_classification(item["title"], item["body"], ["bounty", "external-mirror"], "OWNER", reward), "secondary_source")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_incidental_maintainer_money_mention_is_excluded(self):
        item = self.issue(
            23,
            title="Introductions and project notes",
            body="I have 83 USDC of earned bounty money stuck elsewhere. Please introduce yourself here.",
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "OWNER")
        self.assertEqual(fetch_github.candidate_classification(item["title"], item["body"], [], "OWNER", reward), "incidental_reward_mention")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_maintainer_body_reward_heading_is_actionable(self):
        item = self.issue(
            24,
            title="Add parser regression tests",
            body="Please add the missing tests.\n\n## Bounty: $200\nPaid after maintainer acceptance.",
        )
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        self.assertIsNotNone(record)
        self.assertEqual(record["reward"]["amount"], 200)

    def test_unfunded_or_quarantined_issue_is_excluded(self):
        item = self.issue(
            25,
            title="[QUARANTINED — DO NOT CLAIM] Bounty: $50",
            body="This bounty is not funded or claimable yet.",
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "OWNER")
        self.assertEqual(fetch_github.candidate_classification(item["title"], item["body"], [], "OWNER", reward), "not_actionable")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_actionable_bounty_can_mention_unfunded_state_in_acceptance_criteria(self):
        item = self.issue(
            26,
            title="[DIRECT] Add earning-loop integration",
            body=(
                "## Funded payment contract\n"
                "**Funded and claimable on Base mainnet.**\n"
                "- Solver reward: **2.00 USDC**\n"
                "## Acceptance criteria\n"
                "- Cover claimable, unfunded, verifier-unready, and submitted-not-paid states."
            ),
            labels=["bounty", "funded-live"],
        )
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        self.assertIsNotNone(record)
        self.assertEqual((record["reward"]["amount"], record["reward"]["currency"]), (2, "USDC"))

    def test_dollar_prefixed_stablecoin_is_preserved(self):
        reward = fetch_github.reward_metadata("Bounty: $15 USDC", "", [], "OWNER")
        self.assertEqual((reward["amount"], reward["currency"]), (15, "USDC"))

    def test_solver_reward_is_preferred_over_total_funding(self):
        body = (
            "## Funded payment contract\n"
            "- Confirmed funding: **2.01 / 2.01 USDC**\n"
            "- Solver reward: **2.00 USDC**\n"
            "- Automated verifier reward: **0.01 USDC**"
        )
        reward = fetch_github.reward_metadata("[DIRECT] Add integration", body, ["bounty", "funded-live"], "OWNER")
        self.assertEqual((reward["amount"], reward["currency"]), (2, "USDC"))

    def test_bounty_sequence_number_is_not_treated_as_reward_amount(self):
        title = "Bounty #3 — Robotic laboratory bridge [$20,000 USDC]"
        reward = fetch_github.reward_metadata(title, "## Prize: $20,000 USDC", [], "OWNER")
        self.assertTrue(fetch_github.direct_reward_offer(title, "## Prize: $20,000 USDC"))
        self.assertEqual((reward["amount"], reward["currency"]), (20000, "USDC"))

    def test_colon_amount_without_currency_can_still_mark_direct_offer(self):
        title = "Issue 2: [Bounty:250] Implement image processing"
        body = "This issue and its associated bounty ($250) will close after a maintainer implementation."
        reward = fetch_github.reward_metadata(title, body, [], "OWNER")
        self.assertTrue(fetch_github.direct_reward_offer(title, body))
        self.assertEqual(reward["amount"], 250)

    def test_required_purchase_is_not_reward_amount(self):
        reward = fetch_github.reward_metadata("Bounty available", "Contributor must buy $20 of credits before testing.", [], "OWNER")
        self.assertIsNone(reward["amount"])
        self.assertEqual(reward["provenance"], "unverified")

    def test_label_only_is_unverified(self):
        reward = fetch_github.reward_metadata("Fix typo", "", ["bounty"], "OWNER")
        self.assertIsNone(reward["amount"])
        self.assertEqual(reward["provenance"], "unverified")

    def test_assigned_issue_is_excluded(self):
        item = self.issue(27, assignees=["worker"], title="Bounty: $50")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_mirrored_board_thread_is_excluded(self):
        item = self.issue(
            28,
            title="Bounty: $10",
            body="Worker price: $10\nThis GitHub issue is a mirrored board thread.",
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "OWNER")
        self.assertEqual(
            fetch_github.candidate_classification(item["title"], item["body"], [], "OWNER", reward),
            "secondary_source",
        )
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_in_progress_lifecycle_is_excluded(self):
        item = self.issue(
            29,
            title="Bounty: $20",
            body="Lifecycle: `in_progress`\nSolver reward: 20 USDC",
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "OWNER")
        self.assertEqual(
            fetch_github.candidate_classification(item["title"], item["body"], [], "OWNER", reward),
            "not_actionable",
        )
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_markdown_wrapped_in_progress_lifecycle_is_excluded(self):
        item = self.issue(
            32,
            title="Bounty: $20",
            body="- **Lifecycle:** `in_progress`\n- Solver reward: 20 USDC",
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], ["bounty"], "OWNER")
        self.assertEqual(
            fetch_github.candidate_classification(item["title"], item["body"], ["bounty"], "OWNER", reward),
            "not_actionable",
        )
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_verification_pending_label_is_excluded(self):
        item = self.issue(
            33,
            title="Bounty: $20",
            body="Funded and claimable.\nSolver reward: 20 USDC",
            labels=["bounty", "verification-pending"],
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], ["bounty", "verification-pending"], "OWNER")
        self.assertEqual(
            fetch_github.candidate_classification(
                item["title"], item["body"], ["bounty", "verification-pending"], "OWNER", reward
            ),
            "not_actionable",
        )
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_contributor_payment_requirement_is_excluded(self):
        item = self.issue(
            30,
            title="Bounty: $15 USDC",
            body="Pay: send USDC on Base to payTo.\nAcceptance: $15 USDC tx hash to payTo on this issue.",
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "OWNER")
        self.assertEqual(
            fetch_github.candidate_classification(item["title"], item["body"], [], "OWNER", reward),
            "contributor_payment_required",
        )
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_candidate_reward_budget_is_not_direct_issue_payment(self):
        item = self.issue(
            31,
            title="Cash sprint: find live micro-missions",
            body="## Hard candidate gates\n- reward/budget: **5-50 USDC**\n- listing currently open",
        )
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "OWNER")
        self.assertEqual(
            fetch_github.candidate_classification(item["title"], item["body"], [], "OWNER", reward),
            "incidental_reward_mention",
        )
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_availability_question_is_excluded(self):
        item = self.issue(3, title="Bounty integration question", body="Is this bounty still available? The docs mention a $10 reward.", author_association="NONE")
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "NONE")
        self.assertEqual(fetch_github.candidate_classification(item["title"], item["body"], [], "NONE", reward), "availability_inquiry")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_explicit_unavailable_state_is_excluded(self):
        item = self.issue(4, body="Current work state: `unavailable`\nSolver reward: 6.00 USDC")
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "OWNER")
        self.assertEqual(fetch_github.candidate_classification(item["title"], item["body"], [], "OWNER", reward), "not_actionable")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_reward_word_without_amount_is_not_actionable(self):
        item = self.issue(6, title="Bounty available", body="Please fix the docs.")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_zero_reward_is_not_actionable(self):
        item = self.issue(7, title="Bounty: USD 0", body="Please fix the docs.")
        reward = fetch_github.reward_metadata(item["title"], item["body"], [], "OWNER")
        self.assertEqual(
            fetch_github.candidate_classification(item["title"], item["body"], [], "OWNER", reward),
            "non_positive_reward",
        )
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set()))

    def test_self_repository_is_excluded(self):
        item = self.issue(5, project="yo4e/open-work-radar")
        self.assertIsNone(fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, {"yo4e/open-work-radar"}))

    def test_default_sources_exclude_bounty_alert_indexes(self):
        config = Path(__file__).resolve().parents[1] / "sources.yaml"
        sources, _, _ = fetch_github.load_config(config)
        self.assertTrue(sources)
        for source in sources:
            self.assertIn("-label:bounty-alert", source["query"])

    def test_failed_source_preserves_previous_records(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data.json"
            old = fetch_github.normalize_issue(self.issue(9), "two", "2026-09-08T00:00:00Z", self.NOW, 180, set())
            output.write_text(json.dumps([old]), encoding="utf-8")
            client = FakeClient({"one": [self.issue(1)], "two": fetch_github.SourceFetchError("offline")})
            count, successful, failed = fetch_github.collect(self.sources(directory), output, client, now=self.NOW)
            records = json.loads(output.read_text())
            self.assertEqual((count, successful, failed), (2, 1, ["two"]))
            self.assertEqual({r["issue_number"] for r in records}, {1, 9})

    def test_all_sources_fail_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data.json"
            original = '[{"sentinel": true}]\n'
            output.write_text(original, encoding="utf-8")
            client = FakeClient({"one": fetch_github.SourceFetchError("offline"), "two": fetch_github.SourceFetchError("offline")})
            with self.assertRaises(fetch_github.CollectorError):
                fetch_github.collect(self.sources(directory), output, client, now=self.NOW)
            self.assertEqual(output.read_text(), original)

    def test_body_pr_submitted_is_excluded(self):
        item = self.issue(
            9814,
            project="HHS/simpler-grants-gov",
            title="Bounty: $50",
            body="Please fix docs.\nPR submitted.\nBounty: $50",
        )
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        self.assertIsNotNone(record)
        self.assertIsNone(fetch_github.second_stage(record, item, {}, self.NOW))

    def test_open_pr_fixes_issue_is_excluded(self):
        item = self.issue(14, project="iyeanur6-cyber/ultimate-ai-platform", title="Bounty: $25", body="Bounty: $25")
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        signals = {
            "timeline": [
                {
                    "event": "cross-referenced",
                    "source": {
                        "issue": {
                            "state": "open",
                            "title": "Implement bounty",
                            "body": "Fixes #14",
                            "html_url": "https://github.com/iyeanur6-cyber/ultimate-ai-platform/pull/41",
                            "pull_request": {"url": "https://api.github.com/repos/x/pulls/41"},
                        }
                    },
                }
            ],
            "comments": [],
        }
        self.assertIsNone(fetch_github.second_stage(record, item, signals, self.NOW))

    def test_harmless_pr_mention_does_not_exclude(self):
        item = self.issue(14, project="example/project", title="Bounty: $25", body="Bounty: $25")
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        signals = {
            "timeline": [
                {
                    "event": "cross-referenced",
                    "source": {
                        "issue": {
                            "state": "open",
                            "title": "Docs tweak",
                            "body": "See also #14 for context.",
                            "html_url": "https://github.com/example/project/pull/99",
                            "pull_request": {"url": "https://api.github.com/repos/example/project/pulls/99"},
                        }
                    },
                }
            ],
            "comments": [],
        }
        kept = fetch_github.second_stage(record, item, signals, self.NOW)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "open")

    def test_closed_pr_does_not_exclude(self):
        item = self.issue(14, project="example/project", title="Bounty: $25", body="Bounty: $25")
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        signals = {
            "timeline": [
                {
                    "event": "cross-referenced",
                    "source": {
                        "issue": {
                            "state": "closed",
                            "title": "Attempt",
                            "body": "Fixes #14",
                            "html_url": "https://github.com/example/project/pull/41",
                            "pull_request": {"url": "https://api.github.com/repos/example/project/pulls/41"},
                        }
                    },
                }
            ],
            "comments": [],
        }
        kept = fetch_github.second_stage(record, item, signals, self.NOW)
        self.assertIsNotNone(kept)

    def test_draft_pr_referencing_issue_is_excluded(self):
        item = self.issue(9, project="iyeanur6-cyber/ultimate-ai-platform", title="Bounty: $25", body="Bounty: $25")
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        signals = {
            "timeline": [],
            "comments": [{"body": "Opened draft PR. Fixes #9", "html_url": "https://github.com/x/issues/9#issuecomment-1"}],
        }
        self.assertIsNone(fetch_github.second_stage(record, item, signals, self.NOW))

    def test_expired_deadline_is_excluded(self):
        item = self.issue(
            1609,
            project="moorcheh-ai/memanto",
            title="Bounty: $40",
            body="Please ship the patch.\nBounty: $40\nsubmission deadline of 2026-09-15 23:59 UTC",
        )
        now = dt.datetime(2026, 9, 17, 0, 0, tzinfo=dt.timezone.utc)
        record = fetch_github.normalize_issue(item, "one", "2026-09-17T00:00:00Z", now, 180, set())
        self.assertEqual(record["deadline"], "2026-09-15T23:59:00Z")
        self.assertIsNone(fetch_github.second_stage(record, item, {}, now))

    def test_unclear_bounty_without_maintainer_confirm_is_excluded(self):
        item = self.issue(
            34,
            project="cxlinux-ai/cx-distro",
            title="Bounty: $25",
            body="$25 upon merge. Please confirm.",
        )
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        signals = {
            "comments": [
                {"body": "is the bounty still active?", "author_association": "NONE"},
                {"body": "yeah is this still funded", "author_association": "CONTRIBUTOR"},
            ]
        }
        self.assertTrue(fetch_github.availability_unclear(item["body"], signals))
        self.assertIsNone(fetch_github.second_stage(record, item, signals, self.NOW))

    def test_multi_claim_comments_without_pr_are_kept(self):
        item = self.issue(
            2155,
            project="Scottcjn/rustchain-bounties",
            title="Bounty: $100",
            body="Bounty: $100 for the parser fix.",
        )
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        signals = {
            "comments": [
                {"body": "/attempt #2155", "author_association": "NONE"},
                {"body": "/claim I want this", "author_association": "NONE"},
                {"body": "also /try", "author_association": "CONTRIBUTOR"},
            ],
            "timeline": [],
        }
        kept = fetch_github.second_stage(record, item, signals, self.NOW)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "open")

    def test_timestamp_only_rescan_keeps_truthful_last_checked_at(self):
        item = self.issue(3, project="Henry00IS/ShapeEditor", title="Bounty: $25")
        old = fetch_github.normalize_issue(item, "one", "2026-09-08T00:00:00Z", self.NOW, 180, set())
        new = fetch_github.normalize_issue(item, "one", "2026-09-17T07:00:00Z", self.NOW, 180, set())
        self.assertNotEqual(old["last_checked_at"], new["last_checked_at"])
        stable = fetch_github.apply_freshness([new], [old])
        self.assertEqual(stable[0]["last_checked_at"], "2026-09-17T07:00:00Z")
        self.assertEqual(stable[0]["last_changed_at"], "2026-09-08T00:00:00Z")
        self.assertEqual(fetch_github._substantive(stable[0]), fetch_github._substantive(old))

    def test_search_uses_configured_max_pages(self):
        seen = []

        class TrackingClient:
            def search_issues(self, query, max_pages=1):
                seen.append(max_pages)
                return iter([])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.yaml"
            path.write_text(
                "stale_after_days: 180\nexclude_repositories: []\nsources:\n"
                "  - id: one\n    query: one\n    max_pages: 2\n",
                encoding="utf-8",
            )
            output = Path(directory) / "data.json"
            fetch_github.collect(path, output, TrackingClient(), now=self.NOW)
        self.assertEqual(seen, [2])

    def test_shapeeditor_preserved_when_it_falls_out_of_search_window(self):
        shape = self.issue(3, project="Henry00IS/ShapeEditor", title="Bounty: $25")
        other = self.issue(8, project="example/other", title="Bounty: $40")

        class WindowClient:
            def __init__(self):
                self.calls = {"one": 0, "two": 0}

            def search_issues(self, query, max_pages=1):
                self.calls[query] = self.calls.get(query, 0) + 1
                if query != "one":
                    return iter([])
                if self.calls[query] == 1:
                    return iter([shape])
                return iter([other])

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data.json"
            client = WindowClient()
            fetch_github.collect(self.sources(directory), output, client, now=self.NOW)
            first = json.loads(output.read_text())
            self.assertEqual({r["issue_number"] for r in first}, {3})
            fetch_github.collect(self.sources(directory), output, client, now=self.NOW)
            second = json.loads(output.read_text())
            self.assertEqual({r["issue_number"] for r in second}, {3, 8})
            shape_row = next(r for r in second if r["issue_number"] == 3)
            self.assertEqual(shape_row["project"], "Henry00IS/ShapeEditor")
            self.assertEqual(shape_row["status"], "open")

    def test_unknown_timezone_deadline_is_left_unknown(self):
        self.assertIsNone(fetch_github.parse_deadline("deadline 2026-09-15 23:59 MARS"))
        jst = fetch_github.parse_deadline("submission deadline of 2026-09-15 23:59 JST")
        self.assertEqual(jst, dt.datetime(2026, 9, 15, 14, 59, tzinfo=dt.timezone.utc))
        utc = fetch_github.parse_deadline("submission deadline of 2026-09-15 23:59 UTC")
        self.assertEqual(utc, dt.datetime(2026, 9, 15, 23, 59, tzinfo=dt.timezone.utc))

    def test_lifecycle_fetch_failure_preserves_prior_and_avoids_false_open(self):
        item = self.issue(3, project="Henry00IS/ShapeEditor", title="Bounty: $25")

        class FailLifecycle:
            def search_issues(self, query, max_pages=1):
                return iter([item])

            def lifecycle_signals(self, project, number):
                raise fetch_github.LifecycleUnavailable("timeout")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data.json"
            old = fetch_github.normalize_issue(item, "one", "2026-09-08T00:00:00Z", self.NOW, 180, set())
            output.write_text(json.dumps([old]), encoding="utf-8")
            count, successful, failed = fetch_github.collect(
                self.sources(directory), output, FailLifecycle(), now=self.NOW
            )
            records = json.loads(output.read_text())
            self.assertEqual(failed, [])
            self.assertGreaterEqual(successful, 1)
            self.assertEqual({r["issue_number"] for r in records}, {3})
            self.assertEqual(records[0]["last_checked_at"], "2026-09-08T00:00:00Z")

            blank = Path(directory) / "blank.json"
            fetch_github.collect(self.sources(directory), blank, FailLifecycle(), now=self.NOW)
            self.assertEqual(json.loads(blank.read_text()), [])

    def test_last_page_from_link(self):
        link = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=7>; rel="last"'
        self.assertEqual(fetch_github.last_page_from_link(link), 7)
        self.assertIsNone(fetch_github.last_page_from_link(""))


if __name__ == "__main__":
    unittest.main()
