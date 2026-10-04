import datetime as dt
import unittest

from scripts import fetch_github


class Issue22PrizePoolTests(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 3, 0, 0, tzinfo=dt.timezone.utc)

    def issue(self, number, *, project, title, body, labels=None):
        return {
            "number": number,
            "state": "open",
            "title": title,
            "body": body,
            "html_url": f"https://github.com/{project}/issues/{number}",
            "repository_url": f"https://api.github.com/repos/{project}",
            "labels": [{"name": l} for l in (labels or [])],
            "assignees": [],
            "author_association": "OWNER",
            "created_at": "2026-09-30T03:10:22Z",
            "updated_at": "2026-10-02T12:00:00Z",
        }

    def normalize(self, item):
        return fetch_github.normalize_issue(
            item,
            "bounty",
            fetch_github.iso_z(self.NOW),
            self.NOW,
            180,
            set(),
        )

    def test_hackathon_prize_pool_is_not_treated_as_task_reward(self):
        # Fixture derived from mandaputtra/ping-pong-pay#10
        body = (
            "## What to build\n"
            "Satisfy the Metropolis Hackathon Terms & Conditions.\n\n"
            "## Notes\n"
            "Prize pool is stated as $145,000 in T&C §3.2 but $250,000+ on the website;\n"
            "§3.2 notes sponsor bounties are added later. Plan against the T&C figure.\n"
        )
        item = self.issue(
            10,
            project="mandaputtra/ping-pong-pay",
            title="T&C compliance: AI disclosure, registration, demo video, eligibility",
            body=body,
            labels=["ready-for-agent"],
        )
        result = self.normalize(item)
        self.assertIsNone(result, "Issue explaining hackathon prize pool should not be output as actionable reward offer")

    def test_issue_with_both_prize_pool_and_explicit_bounty(self):
        # Explicit task reward line must take precedence over general prize pool
        body = (
            "## Hackathon Context\n"
            "Event total prize pool is $100,000.\n\n"
            "## Task Reward\n"
            "Bounty: $150 USDC for implementing the bridge contract.\n"
        )
        item = self.issue(
            45,
            project="hackathon/web3-project",
            title="Implement bridge contract",
            body=body,
            labels=["bounty"],
        )
        result = self.normalize(item)
        self.assertIsNotNone(result)
        self.assertEqual(result["reward"]["amount"], 150)
        self.assertEqual(result["reward"]["currency"], "USDC")

    def test_direct_bounty_unaffected(self):
        item = self.issue(
            5,
            project="org/repo",
            title="Bounty: $75 fix memory leak",
            body="Fix leak in websocket connection.\nReward: $75.",
        )
        result = self.normalize(item)
        self.assertIsNotNone(result)
        self.assertEqual(result["reward"]["amount"], 75)
        self.assertEqual(result["reward"]["currency"], "USD")


if __name__ == "__main__":
    unittest.main()
