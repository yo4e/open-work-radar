import datetime as dt
import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path

from scripts import fetch_github
import test_issue14


class Issue22Tests(unittest.TestCase):
    def setUp(self):
        self.helper = test_issue14.Issue14Tests()
        self.now = dt.datetime(2026, 10, 8, tzinfo=dt.timezone.utc)

    def normalize(self, title, body):
        return self.helper.normalize(self.helper.issue(22, title=title, body=body), now=self.now)

    def test_real_compliance_issue_is_not_a_reward_offer(self):
        item = json.loads((Path(__file__).parent / 'fixtures/issue22_compliance.json').read_text())
        self.assertIsNone(self.helper.normalize(item, now=self.now))
        reward = fetch_github.reward_metadata(item['title'], item['body'], [], 'OWNER')
        self.assertIsNone(reward['amount'])
        self.assertFalse(fetch_github.direct_reward_offer(item['title'], item['body']))

    def test_pool_only_title_or_body_has_no_amount_or_direct_offer(self):
        for text in ('Hackathon prize pool: $145,000', 'Prize pool is stated as $145,000 but $250,000+ on the website.',
                     'Hackathon total prize money: $145,000', '$145,000 hackathon prize pool',
                     'Prize pool: 145,000 USD', 'Hackathon PRIZE POOL: $145,000'):
            for title, body in ((text, 'Implement compliance checks.'), ('Compliance checks', text)):
                with self.subTest(title=title, body=body):
                    self.assertIsNone(self.normalize(title, body))
                    self.assertIsNone(fetch_github.reward_metadata(title, body, ['bounty'], 'OWNER')['amount'])
                    self.assertFalse(fetch_github.direct_reward_offer(title, body))

    def test_individual_bounty_survives_pool_context_and_currency_is_preserved(self):
        cases = [
            ('Hackathon prize pool: $145,000', 'Bounty: $500 for this task.', 500, 'USD'),
            ('Hackathon task', 'Prize pool: $145,000\nBounty: $500 for this task.', 500, 'USD'),
            ('Hackathon task', 'Bounty: $500 from the hackathon prize pool of $145,000.', 500, 'USD'),
            ('Bounty: $500 from the hackathon prize pool of $145,000.', 'Implement this task.', 500, 'USD'),
            ('Hackathon task', 'Prize pool: $145,000; Bounty: $500 for this task.', 500, 'USD'),
            ('Hackathon task', '$145,000 prize pool; Bounty: $500 for this task.', 500, 'USD'),
            ('Hackathon task', 'Reward: $150 USDC from the prize pool of $145,000.', 150, 'USDC'),
            ('Hackathon task', 'Bounty: 500 EUR from the prize pool of 145,000 EUR.', 500, 'EUR'),
            ('Bounty: $75 fix memory leak', 'Reward: $75.', 75, 'USD'),
            ('Hackathon task', 'Prize: $100 for solving this issue.', 100, 'USD'),
        ]
        for title, body, amount, currency in cases:
            with self.subTest(title=title, body=body):
                record = self.normalize(title, body)
                self.assertIsNotNone(record)
                self.assertEqual(record['reward']['amount'], amount)
                self.assertEqual(record['reward']['currency'], currency)
                self.assertTrue(fetch_github.direct_reward_offer(title, body))

    def test_pool_amount_after_bounty_does_not_replace_actual_bounty(self):
        text = 'Bounty: $500 from the hackathon prize pool of $145,000.'
        masked = fetch_github.task_reward_text(text)
        self.assertIn('$500', masked)
        self.assertNotIn('$145,000', masked)
        self.assertEqual(len(masked), len(text))
        reward = fetch_github.reward_metadata('Fix parser', text, [], 'OWNER')
        self.assertEqual(reward['amount'], 500)
        self.assertIn('Bounty: $500 from the hackathon prize pool of $145,000', reward['evidence'])

    def test_pool_label_and_incidental_reward_do_not_create_direct_offer(self):
        self.assertIsNone(self.normalize('Compliance checks', 'Prize pool: $145,000.\nDiscuss a bounty of $500.'))

    def test_real_compliance_issue_is_excluded_from_collector_output(self):
        item = json.loads((Path(__file__).parent / 'fixtures/issue22_compliance.json').read_text())
        client = SimpleNamespace(search_issues=lambda *args: iter([item]),
                                 lifecycle_signals=lambda *args: {'comments': [], 'timeline': []})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.json'
            count, _, failed = fetch_github.collect(self.helper.sources(directory), output, client, now=self.now)
            self.assertEqual(count, 0)
            self.assertEqual(failed, [])
            self.assertEqual(json.loads(output.read_text()), [])
