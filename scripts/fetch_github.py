#!/usr/bin/env python3
"""Collect conservative GitHub bounty/reward candidates into normalized JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Protocol

import requests
import yaml

API_URL = "https://api.github.com"
PER_PAGE = 100
BODY_LIMIT = 2000
MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}
REWARD = re.compile(r"\b(?:bounty|bounties|reward|rewards|payout|payouts|compensation|stipend|grant|prize|paid)\b", re.I)
EXPENSE = re.compile(r"\b(?:buy|purchase|cost|fee|fees|credit|credits|subscription|deposit|spend|expense|charge|gas)\b|\b(?:must\s+pay|pay\s+(?:for|to|before))\b", re.I)
UNAVAILABLE = re.compile(r"(?:current\s+work\s+state|lifecycle|work\s+state)\s*[:=-]\s*(?:\*{1,2})?\s*`?(?:unavailable|in[_ -]?progress|claimed|submitted|verification[_ -]?pending)`?|\b(?:bounty|reward)\s+(?:is\s+)?(?:closed|unavailable|no\s+longer\s+available)\b|\bno\s+longer\s+accepting\b", re.I)
QUESTION = re.compile(r"\b(?:is|whether)\s+(?:this|the)\s+(?:bounty|reward)\s+still\s+available\b|\bcould\s+you\s+confirm\b.{0,120}\b(?:bounty|reward)\b.{0,80}\bavailable\b", re.I | re.S)
PROPOSAL = re.compile(
    r"\b(?:proposed|proposal|would\s+you|could\s+you|consider|approve|sponsor)\b.{0,160}\b(?:bounty|reward|paid|payment|compensation)\b"
    r"|\b(?:bounty|reward|paid|payment|compensation)\b.{0,160}\b(?:proposed|proposal|would\s+you|could\s+you|consider|approve|sponsor)\b",
    re.I | re.S,
)
SECONDARY_SOURCE = re.compile(
    r"外部\s*bounty\s*任务镜像"
    r"|###\s*赏金平台\s*/\s*platform\b.{0,400}###\s*原始链接\s*/\s*source url\b"
    r"|\b(?:github\s+)?issue\s+is\s+(?:a\s+)?mirrored\s+(?:board\s+)?thread\b",
    re.I | re.S,
)
EXTERNAL_REFERENCE = re.compile(r"^\s*##\s+(?:current external state|verified live opportunities)\b", re.I | re.M)
NOT_ACTIONABLE_TITLE = re.compile(r"^\s*\[(?:draft|quarantined|unfunded)\b", re.I)
NOT_ACTIONABLE = re.compile(
    r"^\s*(?:>\s*)?(?:\*\*)?unfunded\s+precommit\b"
    r"|\b(?:this\s+issue|this\s+bounty)\s+is\s+not\s+(?:yet\s+)?(?:funded|claimable|live)\b"
    r"|\bnot\s+funded\s+or\s+claimable\b"
    r"|\bfunding\s+needed\s*\.\s*do\s+not\s+start\s+expecting\s+payment\b"
    r"|\bdo\s+not\s+claim\b"
    r"|\bdo\s+not\b.{0,100}\bstart\s+implementation\b"
    r"|\bbecomes\s+paid\s+work\s+only\s+after\b"
    r"|\bdo\s+not\s+announce\b.{0,100}\bas\s+live\b",
    re.I | re.M | re.S,
)
NOT_ACTIONABLE_LABELS = {"funding-needed", "verification-pending"}
INDIRECT = re.compile(r"^\s*\[META\]|\bgross\s+margin\b", re.I)
CONTRIBUTOR_PAYMENT = re.compile(
    r"\bpay\s*:\s*(?:send\s+)?(?:USDC|USDT|USD|EUR|GBP|BTC|ETH|SOL)\b.{0,120}\bto\s+payto\b"
    r"|\btx\s+hash\s+to\s+payto\b",
    re.I | re.S,
)
DIRECT_TITLE_AMOUNT = re.compile(r"\b(?:bounty|reward|prize)\b\s*[:-]?\s*\d[\d,]*(?:\.\d+)?", re.I)
PR_SUBMITTED = re.compile(r"\bPR submitted\b|\bpull request (?:has been )?submitted\b", re.I)
FIXES_ISSUE = re.compile(r"\b(?:fixes|closes|resolves)\s+#(?P<n>\d+)\b", re.I)
DEADLINE = re.compile(
    r"(?:submission\s+)?deadline(?:\s+of|\s*[:=])?\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})(?:[ T](?P<time>\d{2}:\d{2})(?::\d{2})?)?"
    r"(?:\s*(?P<tz>UTC|GMT|Z|JST|EST|EDT|PST|PDT|CST|CDT|MST|MDT|CET|CEST|BST|[+-]\d{2}:?\d{2}))?",
    re.I,
)
KNOWN_TZ_OFFSETS = {
    "UTC": dt.timedelta(0),
    "GMT": dt.timedelta(0),
    "Z": dt.timedelta(0),
    "JST": dt.timedelta(hours=9),
    "EST": dt.timedelta(hours=-5),
    "EDT": dt.timedelta(hours=-4),
    "PST": dt.timedelta(hours=-8),
    "PDT": dt.timedelta(hours=-7),
    "CST": dt.timedelta(hours=-6),
    "CDT": dt.timedelta(hours=-5),
    "MST": dt.timedelta(hours=-7),
    "MDT": dt.timedelta(hours=-6),
    "CET": dt.timedelta(hours=1),
    "CEST": dt.timedelta(hours=2),
    "BST": dt.timedelta(hours=1),
}
TZ_TAIL = re.compile(r"\s*([A-Za-z]{2,5}|[+-]\d{2}:?\d{2})")
LINK_LAST = re.compile(r'<[^>]*[?&]page=(\d+)[^>]*>\s*;\s*rel="last"', re.I)
STILL_ACTIVE_Q = re.compile(
    r"\b(?:is|whether)\s+(?:this|the)\s+bounty\s+still\s+(?:active|available|funded)\b"
    r"|\bstill\s+(?:active|available)\b.{0,60}\bbounty\b"
    r"|\bbounty\s+still\s+(?:active|available)\b",
    re.I,
)
MAINTAINER_CONFIRM = re.compile(
    r"\b(?:still\s+(?:active|available|funded|open)|bounty\s+is\s+(?:still\s+)?(?:active|open|funded))\b",
    re.I,
)
CLAIM_ONLY = re.compile(r"(?:^|\s)(?:/try|/attempt|/claim)\b", re.I)
DIRECT_REWARD_PREFIX = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
    r"(?:bounty|reward(?!/)|prize|payment|payout|compensation|solver\s+reward|target\s+solver\s+reward)\b",
    re.I,
)
CURRENCY = r"(?:US\$|\$|USD|CAD|AUD|EUR|€|GBP|£|JPY|USDC|USDT|BTC|ETH|SOL)"
AMOUNT = r"(?:\d[\d,]*(?:\.\d{1,8})?)"
MONEY = (
    re.compile(rf"(?<![A-Za-z0-9])(?P<currency>{CURRENCY})\s*(?P<amount>{AMOUNT})(?![A-Za-z0-9])", re.I),
    re.compile(rf"(?<![A-Za-z0-9])(?P<amount>{AMOUNT})\s*(?P<currency>{CURRENCY})(?![A-Za-z0-9])", re.I),
)


class CollectorError(RuntimeError):
    pass


class SourceFetchError(CollectorError):
    pass


class LifecycleUnavailable(CollectorError):
    """Lifecycle evidence could not be fetched; do not treat as 'no PRs'."""

    pass


class SearchClient(Protocol):
    def search_issues(self, query: str, max_pages: int = 1) -> Iterable[dict[str, Any]]: ...


class LifecycleClient(Protocol):
    def lifecycle_signals(self, project: str, number: int) -> dict[str, Any]: ...


class GitHubClient:
    def __init__(self, token: str | None = None, api_url: str = API_URL, timeout: int = 30) -> None:
        self.api_url, self.timeout = api_url.rstrip("/"), timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/vnd.github+json", "User-Agent": "open-work-radar/0.1a", "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def search_issues(self, query: str, max_pages: int = 1) -> Iterable[dict[str, Any]]:
        for page in range(1, max_pages + 1):
            try:
                response = self.session.get(f"{self.api_url}/search/issues", params={"q": query, "sort": "updated", "order": "desc", "per_page": PER_PAGE, "page": page}, timeout=self.timeout)
            except requests.RequestException as exc:
                raise SourceFetchError(str(exc)) from exc
            if not response.ok:
                reset = response.headers.get("X-RateLimit-Reset")
                suffix = f"; rate-limit reset epoch {reset}" if reset else ""
                raise SourceFetchError(f"GitHub API HTTP {response.status_code}{suffix}: {response.text[:500]}")
            payload = response.json()
            items = payload.get("items")
            if not isinstance(items, list):
                raise SourceFetchError(f"search response for {query!r} did not contain items")
            yield from (item for item in items if isinstance(item, dict))
            total = payload.get("total_count")
            if not items or len(items) < PER_PAGE or (isinstance(total, int) and page * PER_PAGE >= total):
                break

    def lifecycle_signals(self, project: str, number: int) -> dict[str, Any]:
        """Newest-biased comment/timeline fetch for retained candidates only.

        GitHub lists comments/timeline oldest-first. A single first page can miss
        the current work state on busy issues, so we also fetch the last page when
        Link headers say more exist. HTTP/network failure raises rather than
        returning empty evidence.
        """
        if "/" not in project:
            return {"comments": [], "timeline": []}
        owner, repo = project.split("/", 1)
        try:
            comments = self._newest_biased(
                f"{self.api_url}/repos/{owner}/{repo}/issues/{number}/comments",
                per_page=100,
            )
            timeline = self._newest_biased(
                f"{self.api_url}/repos/{owner}/{repo}/issues/{number}/timeline",
                per_page=100,
                headers={"Accept": "application/vnd.github+json"},
            )
        except (requests.RequestException, LifecycleUnavailable) as exc:
            raise LifecycleUnavailable(str(exc)) from exc
        return {"comments": comments, "timeline": timeline}

    def _newest_biased(self, url: str, per_page: int = 100, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
        try:
            first = self.session.get(url, params={"per_page": per_page, "page": 1}, timeout=self.timeout, headers=headers)
        except requests.RequestException as exc:
            raise LifecycleUnavailable(str(exc)) from exc
        if not first.ok:
            raise LifecycleUnavailable(f"GitHub API HTTP {first.status_code}: {first.text[:300]}")
        rows = first.json()
        if not isinstance(rows, list):
            raise LifecycleUnavailable(f"expected a list from {url}")
        items = [x for x in rows if isinstance(x, dict)]
        last_page = last_page_from_link(first.headers.get("Link") or "")
        if last_page and last_page > 1:
            try:
                last = self.session.get(
                    url, params={"per_page": per_page, "page": last_page}, timeout=self.timeout, headers=headers
                )
            except requests.RequestException as exc:
                raise LifecycleUnavailable(str(exc)) from exc
            if not last.ok:
                raise LifecycleUnavailable(f"GitHub API HTTP {last.status_code}: {last.text[:300]}")
            extra = last.json()
            if not isinstance(extra, list):
                raise LifecycleUnavailable(f"expected a list from {url} page {last_page}")
            seen = {id(x) for x in items}
            keys = {(x.get("id"), x.get("url"), x.get("html_url")) for x in items}
            for row in extra:
                if not isinstance(row, dict):
                    continue
                key = (row.get("id"), row.get("url"), row.get("html_url"))
                if key in keys or id(row) in seen:
                    continue
                items.append(row)
                keys.add(key)
        return items


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_z(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def last_page_from_link(link_header: str) -> int | None:
    match = LINK_LAST.search(link_header or "")
    if not match:
        return None
    try:
        page = int(match.group(1))
    except ValueError:
        return None
    return page if page > 0 else None


def parse_time(value: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def compact(text: str, limit: int = BODY_LIMIT) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def load_config(path: Path) -> tuple[list[dict[str, Any]], int, set[str]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CollectorError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise CollectorError("sources.yaml must contain a sources list")
    stale = int(raw.get("stale_after_days", 180))
    excluded = {str(x).lower() for x in raw.get("exclude_repositories", [])}
    sources, seen = [], set()
    for item in raw["sources"]:
        source_id, query = str(item.get("id", "")).strip(), str(item.get("query", "")).strip()
        pages = int(item.get("max_pages", 2))
        if not source_id or not query or source_id in seen or not 1 <= pages <= 10:
            raise CollectorError(f"invalid source entry: {item!r}")
        seen.add(source_id)
        sources.append({"id": source_id, "query": query, "max_pages": pages})
    return sources, stale, excluded


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectorError(f"invalid existing output; refusing to overwrite {path}") from exc
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise CollectorError("existing output must be a JSON list")
    return value


def repo_name(item: dict[str, Any]) -> str:
    match = re.search(r"/repos/([^/]+/[^/]+)$", str(item.get("repository_url") or ""))
    return match.group(1) if match else "unknown/unknown"


def distance(pattern: re.Pattern[str], text: str, pos: int, radius: int = 120) -> int | None:
    matches = list(pattern.finditer(text, max(0, pos - radius), min(len(text), pos + radius)))
    return min((min(abs(pos - m.start()), abs(pos - m.end())) for m in matches), default=None)


def reward_from_match(match: re.Match[str], text: str, association: str) -> dict[str, Any]:
    amount = float(match.group("amount").replace(",", ""))
    amount = int(amount) if amount.is_integer() else amount
    currency_token = match.group("currency").upper()
    if currency_token in {"$", "US$"}:
        stablecoin = re.match(r"\s*(USDC|USDT)\b", text[match.end():match.end() + 12], re.I)
        currency = stablecoin.group(1).upper() if stablecoin else "USD"
    else:
        currency = {"€": "EUR", "£": "GBP"}.get(currency_token, currency_token)
    start, end = max(0, match.start() - 100), min(len(text), match.end() + 100)
    return {
        "amount": amount,
        "currency": currency,
        "provenance": "stated" if association in MAINTAINERS else "unverified",
        "verified": False,
        "evidence": compact(text[start:end], 260),
    }


def reward_metadata(title: str, body: str, labels: list[str], association: str) -> dict[str, Any]:
    text, label_text = f"{title}\n{body}", " ".join(labels)

    title_matches = sorted((m for pattern in MONEY for m in pattern.finditer(title)), key=lambda m: m.start())
    for match in title_matches:
        reward_d = distance(REWARD, title, match.start(), radius=80)
        if reward_d is not None and reward_d <= 50:
            return reward_from_match(match, title, association)

    for line in body.splitlines():
        if not DIRECT_REWARD_PREFIX.search(line):
            continue
        line_matches = sorted((m for pattern in MONEY for m in pattern.finditer(line)), key=lambda m: m.start())
        if line_matches:
            return reward_from_match(line_matches[0], line, association)

    matches = sorted((m for pattern in MONEY for m in pattern.finditer(text)), key=lambda m: m.start())
    for match in matches:
        reward_d, expense_d = distance(REWARD, text, match.start()), distance(EXPENSE, text, match.start())
        if reward_d is None or (expense_d is not None and expense_d <= reward_d):
            continue
        return reward_from_match(match, text, association)

    signal = REWARD.search(text)
    if signal:
        return {"amount": None, "currency": None, "provenance": "unverified", "verified": False, "evidence": compact(text[max(0, signal.start()-80):signal.end()+120], 240)}
    if REWARD.search(label_text):
        return {"amount": None, "currency": None, "provenance": "unverified", "verified": False, "evidence": f"label signal: {compact(label_text, 160)}"}
    return {"amount": None, "currency": None, "provenance": "unknown", "verified": False, "evidence": None}


def category(title: str, labels: list[str]) -> str:
    text = f"{title} {' '.join(labels)}".lower()
    for value, terms in (("translation", ("translation", "localization", "i18n", "l10n")), ("docs", ("documentation", "docs", "readme")), ("design", ("design", "ux", "ui")), ("security", ("security", "vulnerability", "cve"))):
        if any(term in text for term in terms):
            return value
    return "unknown"


def direct_reward_offer(title: str, body: str) -> bool:
    if DIRECT_TITLE_AMOUNT.search(title):
        return True
    for pattern in MONEY:
        for match in pattern.finditer(title):
            reward_d = distance(REWARD, title, match.start(), radius=80)
            if reward_d is not None and reward_d <= 50:
                return True
    for line in body.splitlines():
        if DIRECT_REWARD_PREFIX.search(line) and any(pattern.search(line) for pattern in MONEY):
            return True
    return False


def candidate_classification(title: str, body: str, labels: list[str], association: str, reward: dict[str, Any]) -> str:
    text = f"{title}\n{body}"
    label_set = {label.lower() for label in labels}
    if reward["amount"] is None:
        return "no_explicit_reward_amount"
    if float(reward["amount"]) <= 0:
        return "non_positive_reward"
    if "external-mirror" in label_set or "bounty-alert" in label_set or SECONDARY_SOURCE.search(text):
        return "secondary_source"
    if QUESTION.search(text):
        return "availability_inquiry"
    if UNAVAILABLE.search(text) or NOT_ACTIONABLE_TITLE.search(title) or NOT_ACTIONABLE.search(text) or label_set & NOT_ACTIONABLE_LABELS:
        return "not_actionable"
    if association not in MAINTAINERS:
        return "contributor_proposal" if PROPOSAL.search(text) else "third_party_claim"
    if CONTRIBUTOR_PAYMENT.search(body):
        return "contributor_payment_required"
    if INDIRECT.search(title):
        return "indirect_or_meta"
    if EXTERNAL_REFERENCE.search(body):
        return "secondary_reference"
    if not direct_reward_offer(title, body):
        return "incidental_reward_mention"
    return "maintainer_reward_offer"


def normalize_issue(item: dict[str, Any], source_id: str, checked_at: str, now: dt.datetime, stale_days: int, excluded: set[str]) -> dict[str, Any] | None:
    if str(item.get("state", "")).lower() != "open" or item.get("pull_request"):
        return None
    updated = parse_time(item.get("updated_at"))
    if stale_days and updated and now - updated > dt.timedelta(days=stale_days):
        return None
    project = repo_name(item)
    if project.lower() in excluded:
        return None
    number, url = item.get("number"), str(item.get("html_url") or "")
    if not isinstance(number, int) or not url:
        return None
    title, body = str(item.get("title") or ""), str(item.get("body") or "")
    assignees = sorted(str(x.get("login")) for x in item.get("assignees", []) if isinstance(x, dict) and x.get("login"))
    if assignees:
        return None
    labels = sorted(str(x.get("name")) for x in item.get("labels", []) if isinstance(x, dict) and x.get("name"))
    association = str(item.get("author_association") or "NONE").upper()
    reward = reward_metadata(title, body, labels, association)
    if candidate_classification(title, body, labels, association, reward) != "maintainer_reward_offer":
        return None
    deadline = parse_deadline(body, title)
    return {
        "id": f"github-{project.replace('/', '-')}-{number}", "source": "github", "source_url": url,
        "title": title, "project": project, "issue_number": number, "category": category(title, labels),
        "status": "open", "github_state": "open",
        "reward": reward, "difficulty": "unknown", "ai_assistability": "unknown",
        "deadline": iso_z(deadline) if deadline else None,
        "competition": {"attempts": None, "claims": None, "open_prs": None},
        "assignees": assignees,
        "labels": labels, "author_association": association, "body_excerpt": compact(body),
        "published_at": item.get("created_at"), "updated_at": item.get("updated_at"), "last_checked_at": checked_at,
        "discovery_sources": [source_id], "notes": None,
    }


def _tz_offset(token: str) -> dt.timedelta | None:
    raw = (token or "").strip()
    if not raw:
        return dt.timedelta(0)
    named = KNOWN_TZ_OFFSETS.get(raw.upper())
    if named is not None:
        return named
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", raw)
    if not match:
        return None
    sign = 1 if match.group(1) == "+" else -1
    return dt.timedelta(hours=sign * int(match.group(2)), minutes=sign * int(match.group(3)))


def parse_deadline(body: str, title: str = "") -> dt.datetime | None:
    """Parse a deadline only when the timezone is explicit and understood.

    Unknown trailing timezone text (for example `JST` if it were not mapped, or
    `FOO`) must not be silently treated as UTC.
    """
    text = f"{title}\n{body}"
    match = DEADLINE.search(text)
    if not match:
        return None
    dangling = TZ_TAIL.match(text[match.end():])
    tz = match.group("tz")
    if dangling and not tz:
        return None
    offset = _tz_offset(tz or "")
    if offset is None:
        return None
    date, time_part = match.group("date"), match.group("time") or "23:59"
    try:
        naive = dt.datetime.fromisoformat(f"{date}T{time_part}:00")
    except ValueError:
        return None
    return (naive - offset).replace(tzinfo=dt.timezone.utc)


def _source_issue(event: dict[str, Any]) -> dict[str, Any]:
    src = event.get("source") or {}
    issue = src.get("issue") if isinstance(src, dict) else None
    return issue if isinstance(issue, dict) else {}


def _pr_attaches_work(issue_number: int, src: dict[str, Any], event: dict[str, Any]) -> bool:
    text = " ".join(
        str(x or "")
        for x in (src.get("title"), src.get("body"), event.get("body"), src.get("html_url"))
    )
    hit = FIXES_ISSUE.search(text)
    return bool(hit) and int(hit.group("n")) == issue_number


def linked_work_prs(issue_number: int, body: str, signals: dict[str, Any]) -> list[str]:
    """Currently active work PRs attached to this issue.

    A timeline cross-reference whose source happens to be a PR is not enough:
    closed/rejected PRs and mere mentions must not suppress an actionable issue.
    """
    found: list[str] = []
    if PR_SUBMITTED.search(body or ""):
        found.append("body:PR submitted")
    for ev in signals.get("timeline") or []:
        if not isinstance(ev, dict):
            continue
        src = _source_issue(ev)
        if not src.get("pull_request"):
            continue
        if str(src.get("state") or "").lower() != "open":
            continue
        if not _pr_attaches_work(issue_number, src, ev):
            continue
        found.append(str(src.get("html_url") or src.get("url") or "timeline-pr"))
    for comment in signals.get("comments") or []:
        text = str(comment.get("body") or "")
        hit = FIXES_ISSUE.search(text)
        if hit and int(hit.group("n")) == issue_number:
            found.append(str(comment.get("html_url") or "comment-fixes"))
    return found


def availability_unclear(body: str, signals: dict[str, Any]) -> bool:
    comments = signals.get("comments") or []
    questions = STILL_ACTIVE_Q.search(body or "")
    maintainer_ok = False
    for comment in comments:
        text = str(comment.get("body") or "")
        assoc = str(comment.get("author_association") or "NONE").upper()
        if STILL_ACTIVE_Q.search(text):
            questions = True
        if assoc in MAINTAINERS and MAINTAINER_CONFIRM.search(text):
            maintainer_ok = True
    return bool(questions) and not maintainer_ok


def second_stage(record: dict[str, Any], item: dict[str, Any], signals: dict[str, Any], now: dt.datetime) -> dict[str, Any] | None:
    """Comment/PR/deadline checks on first-stage keepers only. Multi-claim comments do not exclude."""
    body = str(item.get("body") or record.get("body_excerpt") or "")
    number = int(record["issue_number"])
    deadline = parse_deadline(body, str(record.get("title") or ""))
    if deadline and deadline < now:
        return None
    if deadline:
        record = dict(record)
        record["deadline"] = iso_z(deadline)
    if linked_work_prs(number, body, signals):
        return None
    if availability_unclear(body, signals):
        record = dict(record)
        record["status"] = "unclear"
        return None
    return record


def _substantive(record: dict[str, Any]) -> str:
    payload = {k: v for k, v in record.items() if k not in {"last_checked_at", "last_changed_at"}}
    return json.dumps(payload, sort_keys=True, default=str)


def apply_freshness(records: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep last_checked_at as the real check time; track last_changed_at separately."""
    old = {r.get("source_url"): r for r in existing if r.get("source_url")}
    out = []
    for rec in records:
        rec = dict(rec)
        prev = old.get(rec.get("source_url"))
        checked = rec.get("last_checked_at")
        if prev and _substantive(prev) == _substantive(rec):
            rec["last_changed_at"] = prev.get("last_changed_at") or prev.get("last_checked_at")
        else:
            rec["last_changed_at"] = checked
        out.append(rec)
    return out


def stabilize_checked_at(records: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Back-compat alias: freshness is truthful; material change time is separate."""
    return apply_freshness(records, existing)


def merge_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        url = record["source_url"]
        if url not in merged:
            merged[url] = dict(record)
        else:
            sources = set(merged[url].get("discovery_sources", [])) | set(record.get("discovery_sources", []))
            merged[url].update(record)
            merged[url]["discovery_sources"] = sorted(sources)
    return sorted(merged.values(), key=lambda x: x["source_url"])


def write_json(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
    temp = Path(handle.name)
    try:
        with handle:
            json.dump(records, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise CollectorError(f"could not write {path}: {exc}") from exc


def collect(sources_path: Path, output_path: Path, client: SearchClient, now: dt.datetime | None = None) -> tuple[int, int, list[str]]:
    sources, stale, excluded = load_config(sources_path)
    existing, now = load_existing(output_path), now or now_utc()
    fresh, successful, failed = [], set(), []
    evaluated_urls: set[str] = set()
    lifecycle_blocked: set[str] = set()
    for source in sources:
        try:
            kept = []
            for item in client.search_issues(source["query"], source["max_pages"]):
                rec = normalize_issue(item, source["id"], iso_z(now), now, stale, excluded)
                url = str((rec or {}).get("source_url") or item.get("html_url") or "")
                signals = item.get("_lifecycle")
                getter = getattr(client, "lifecycle_signals", None)
                lifecycle_failed = False
                if rec and signals is None and callable(getter):
                    try:
                        signals = getter(rec["project"], rec["issue_number"]) or {}
                    except Exception:
                        lifecycle_failed = True
                if rec and lifecycle_failed:
                    if url:
                        lifecycle_blocked.add(url)
                    continue
                if rec:
                    if url:
                        evaluated_urls.add(url)
                    rec = second_stage(rec, item, signals or {}, now)
                    if rec:
                        kept.append(rec)
                elif url:
                    evaluated_urls.add(url)
            records = kept
            fresh.extend(records)
            successful.add(source["id"])
            print(f"{source['id']}: kept {len(records)} candidates", file=sys.stderr)
        except SourceFetchError as exc:
            failed.append(source["id"])
            print(f"warning: {source['id']} failed: {exc}", file=sys.stderr)
    if not successful:
        raise CollectorError("all sources failed; existing output was left unchanged" if existing else "all sources failed; no output was written")
    preserved = []
    for old in existing:
        url = old.get("source_url")
        if not url or url in evaluated_urls:
            continue
        srcs = set(old.get("discovery_sources") or [])
        if url in lifecycle_blocked or srcs & set(failed) or srcs & successful or not srcs:
            preserved.append(old)
    output = merge_records([*fresh, *preserved])
    output = apply_freshness(output, existing)
    write_json(output_path, output)
    return len(output), len(successful), failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path("sources.yaml"))
    parser.add_argument("--output", type=Path, default=Path("data/opportunities.json"))
    args = parser.parse_args(argv)
    try:
        count, successful, failed = collect(args.sources, args.output, GitHubClient(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")))
    except CollectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {count} opportunities from {successful} successful sources" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
