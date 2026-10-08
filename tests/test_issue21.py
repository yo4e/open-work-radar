"""Reduced real API pages fetched 2026-10-08; see fixtures/README.md."""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scripts import fetch_github, fetch_opire
import test_issue14
import test_issue18


class Response:
    ok = True
    headers = {}

    def __init__(self, rows, last=False):
        self.rows = rows
        self.headers = {'Link': '<https://api.github.com/timeline?per_page=100&page=4>; rel="last"'} if last else {}

    def json(self):
        return copy.deepcopy(self.rows)


class Session:
    headers = {}

    def __init__(self, first, last):
        self.first, self.last, self.calls = first, last, []

    def get(self, url, params=None, **kwargs):
        page = params['page']
        self.calls.append((url, page))
        if url.endswith('/comments'):
            return Response([{'id': 1, 'body': 'PR submitted. Fixes #7', 'author_association': 'NONE'}])
        return Response(self.first if page == 1 else self.last, last=page == 1)


class Issue21Tests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / 'fixtures'
        self.first = json.loads((root / 'issue21_timeline_page1.json').read_text())
        self.last = json.loads((root / 'issue21_timeline_page4.json').read_text())
        self.helper = test_issue14.Issue14Tests()
        self.item = self.helper.issue(7, project='flowese/UdioWrapper', updated_at='2026-10-02T00:00:00Z')
        self.now = dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc)

    def client(self, first=None, last=None):
        client = fetch_github.GitHubClient()
        client.session = Session(self.first if first is None else first, self.last if last is None else last)
        client.search_issues = lambda *args: iter([self.item])
        return client

    def test_real_pages_keep_both_work_prs(self):
        client = self.client()
        signals = client.lifecycle_signals('flowese/UdioWrapper', 7)
        prs = {x['source']['issue']['number'] for x in signals['timeline']}
        self.assertTrue({42, 50} <= prs)
        self.assertEqual([page for url, page in client.session.calls if url.endswith('/timeline')], [1, 4])

    def test_real_page_collection_suppresses_github_and_opire(self):
        client = self.client()
        signals = client.lifecycle_signals('flowese/UdioWrapper', 7)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.json'
            count, _, failed = fetch_github.collect(self.helper.sources(directory), output, client, now=self.now)
            self.assertEqual(count, 0)
            self.assertEqual(failed, [])
            self.assertEqual(json.loads(output.read_text()), [])
        reward = test_issue18.Issue18Tests().reward(url=self.item['html_url'], cents=2000)
        self.assertIsNone(fetch_opire.normalize_opire_reward(reward, self.item, signals,
            checked_at=fetch_github.iso_z(self.now), now=self.now, stale_days=180, excluded=set()))

    def test_closed_and_unrelated_prs_keep_candidate_through_collection(self):
        last = copy.deepcopy(self.last)
        last[0]['source']['issue']['state'] = 'closed'
        last[1]['source']['issue'].update(title='Unrelated change', body='Fixes #99')
        with tempfile.TemporaryDirectory() as directory:
            count, _, _ = fetch_github.collect(self.helper.sources(directory), Path(directory) / 'out.json',
                self.client(last=last), now=self.now)
            self.assertEqual(count, 1)

    def test_overlap_deduplicates_but_unknown_identity_and_distinct_times_survive(self):
        event = self.last[0]
        later = copy.deepcopy(event)
        later['created_at'] = '2026-10-08T00:00:00Z'
        unknown = {'event': 'cross-referenced', 'source': {'issue': {'body': 'Fixes #7'}}}
        comment = {'id': 3, 'event': 'commented', 'body': 'hello'}
        rows = self.client(first=[event, unknown, comment], last=[event, later, unknown, comment])._newest_biased('timeline')
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows.count(unknown), 2)

    def test_source_url_fallback_and_event_kind_are_part_of_identity(self):
        for field in ('url', 'html_url'):
            event = {'event': 'cross-referenced', 'created_at': '2026-10-08T00:00:00Z',
                     'source': {'issue': {field: 'https://example.test/1'}}}
            other = copy.deepcopy(event)
            other['event'] = 'referenced'
            rows = self.client(first=[event], last=[event, other])._newest_biased('timeline')
            self.assertEqual(len(rows), 2)
