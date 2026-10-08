# Regression fixtures

Fetched read-only from the public GitHub API on 2026-10-08. These JSON files are
test data; no code in issue or PR bodies is executed.

- `issue21_timeline_page1.json`: selected cross-reference events from
  https://api.github.com/repos/flowese/UdioWrapper/issues/7/timeline?per_page=100&page=1
- `issue21_timeline_page4.json`: PR #42/#50 cross-reference events from
  https://api.github.com/repos/flowese/UdioWrapper/issues/7/timeline?per_page=100&page=4

Both timeline files omit unrelated events and unused actor/repository fields.
The retained event timestamps and source issue ID, URLs, state, title, body,
draft flag, and PR fields are from the API. Crucially, the real cross-reference
events have no top-level `id`, `url`, or `html_url`. The fake Link header models
four pages; tests assert that only pages 1 and 4 are requested. Intermediate-page
coverage remains outside this fix.

`issue22_compliance.json` contains the current title and full body of
https://github.com/mandaputtra/ping-pong-pay/issues/10, with the minimal issue
envelope required by the normalizer. Its Notes quote $145,000 and $250,000+
event totals, rather than an individual task reward. The author association is
set to OWNER and the ready-for-agent label is retained to exercise the
maintainer-offer false-positive path regardless of incidental API metadata.

Additional synthetic cases in `test_issue21.py` and `test_issue22.py` cover
overlapping pages, missing identities, closed/unrelated PRs, title-only pools,
same-line task offers, and currencies. Existing issue #14/#17 tests cover
ordinary comments, PR-submitted hints, multi-claim offers, and meta/mirror cases.
