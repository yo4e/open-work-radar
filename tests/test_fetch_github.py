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

    def issue(self, number, *, state="open", title="Docs bounty: $25", body="Please update the README.", labels=None, author_association="OWNER", project="example/project", updated_at="2026-09-08T00:00:00Z"):
        return {
            "number": number, "state": state, "title": title, "body": body,
            "html_url": f"https://github.com/{project}/issues/{number}",
            "repository_url": f"https://api.github.com/repos/{project}",
            "labels": [{"name": label} for label in (labels or [])], "assignees": [],
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

    def test_required_purchase_is_not_reward_amount(self):
        reward = fetch_github.reward_metadata("Bounty available", "Contributor must buy $20 of credits before testing.", [], "OWNER")
        self.assertIsNone(reward["amount"])
        self.assertEqual(reward["provenance"], "unverified")

    def test_label_only_is_unverified(self):
        reward = fetch_github.reward_metadata("Fix typo", "", ["bounty"], "OWNER")
        self.assertIsNone(reward["amount"])
        self.assertEqual(reward["provenance"], "unverified")

    def test_availability_question_is_unclear(self):
        item = self.issue(3, title="Bounty integration question", body="Is this bounty still available? The docs mention a $10 reward.", author_association="NONE")
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        self.assertEqual(record["status"], "unclear")
        self.assertEqual(record["difficulty"], "unknown")
        self.assertEqual(record["ai_assistability"], "unknown")

    def test_explicit_unavailable_state_is_unclear(self):
        item = self.issue(4, body="Current work state: `unavailable`\nSolver reward: 6.00 USDC")
        record = fetch_github.normalize_issue(item, "one", "2026-09-09T01:00:00Z", self.NOW, 180, set())
        self.assertEqual(record["status"], "unclear")

    def test_reward_word_without_amount_is_not_actionable(self):
        item = self.issue(6, title="Bounty available", body="Please fix the docs.")
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


if __name__ == "__main__":
    unittest.main()
