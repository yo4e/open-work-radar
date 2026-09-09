import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import fetch_github  # noqa: E402


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.queries = []

    def search_issues(self, query, max_pages=2):
        self.queries.append(query)
        response = self.responses[query]
        if isinstance(response, Exception):
            raise response
        return iter(response)


class CollectorTests(unittest.TestCase):
    NOW = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc)

    def issue(self, number, *, state="open", title="Paid docs", body="Reward: $25", labels=None):
        return {
            "number": number,
            "state": state,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/example/project/issues/{number}",
            "repository_url": "https://api.github.com/repos/example/project",
            "labels": [{"name": label} for label in (labels or ["bounty"])],
            "assignees": [],
            "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-08T00:00:00Z",
        }

    def sources_file(self, directory):
        path = Path(directory) / "sources.yaml"
        path.write_text(
            "stale_after_days: 180\nsources:\n  - id: one\n    query: one\n  - id: two\n    query: two\n",
            encoding="utf-8",
        )
        return path

    def test_closed_issues_are_excluded_and_overlapping_results_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self.sources_file(directory)
            output = root / "data" / "opportunities.json"
            shared = self.issue(1)
            client = FakeClient({"one": [shared, self.issue(2, state="closed")], "two": [shared]})

            count, source_count, failed = fetch_github.collect(sources, output, client, now=self.NOW)
            records = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual((count, source_count, failed), (1, 2, []))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["discovery_sources"], ["one", "two"])
            self.assertEqual(records[0]["reward"]["verified"], False)

    def test_failed_source_preserves_its_previous_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self.sources_file(directory)
            output = root / "data" / "opportunities.json"
            old = fetch_github.normalize_issue(
                self.issue(9, title="Old reward"), "two", "2026-09-07T00:00:00Z", self.NOW, 180
            )
            output.parent.mkdir()
            output.write_text(json.dumps([old]), encoding="utf-8")
            client = FakeClient({"one": [self.issue(1)], "two": fetch_github.SourceFetchError("offline")})

            count, source_count, failed = fetch_github.collect(sources, output, client, now=self.NOW)
            records = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual((count, source_count, failed), (2, 1, ["two"]))
            self.assertEqual({record["source_url"] for record in records}, {
                "https://github.com/example/project/issues/1",
                "https://github.com/example/project/issues/9",
            })

    def test_reward_parser_does_not_read_amounts_from_hashes(self):
        metadata = fetch_github.reward_metadata(
            "Micro bounty alert",
            "Reward: CAD24. Draft commit 97ca54895cad24d574ce7b3850c8380c709dc0e2.",
            [],
        )
        self.assertEqual(metadata["amount"], 24)
        self.assertEqual(metadata["currency"], "CAD")
        self.assertEqual(metadata["provenance"], "stated")

    def test_reward_parser_does_not_treat_required_credit_purchase_as_reward(self):
        metadata = fetch_github.reward_metadata(
            "Bounty available",
            "Contributor must buy $20 of credits before testing.",
            [],
        )
        self.assertIsNone(metadata["amount"])
        self.assertIsNone(metadata["currency"])
        self.assertEqual(metadata["provenance"], "unverified")
        self.assertFalse(metadata["verified"])

    def test_atomic_write_keeps_existing_file_when_all_sources_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self.sources_file(directory)
            output = root / "data" / "opportunities.json"
            output.parent.mkdir()
            original = "[{\"sentinel\": true}]\n"
            output.write_text(original, encoding="utf-8")
            client = FakeClient({"one": fetch_github.SourceFetchError("offline"), "two": fetch_github.SourceFetchError("offline")})

            with self.assertRaises(fetch_github.CollectorError):
                fetch_github.collect(sources, output, client, now=self.NOW)
            self.assertEqual(output.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
