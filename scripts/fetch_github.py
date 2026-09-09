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
UNAVAILABLE = re.compile(r"(?:current\s+work\s+state|lifecycle|work\s+state)\s*[:=-]\s*`?unavailable`?|\b(?:bounty|reward)\s+(?:is\s+)?(?:closed|unavailable|no\s+longer\s+available)\b|\bno\s+longer\s+accepting\b", re.I)
QUESTION = re.compile(r"\b(?:is|whether)\s+(?:this|the)\s+(?:bounty|reward)\s+still\s+available\b|\bcould\s+you\s+confirm\b.{0,120}\b(?:bounty|reward)\b.{0,80}\bavailable\b", re.I | re.S)
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


class SearchClient(Protocol):
    def search_issues(self, query: str, max_pages: int = 1) -> Iterable[dict[str, Any]]: ...


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


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_z(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


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
        pages = int(item.get("max_pages", 1))
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


def reward_metadata(title: str, body: str, labels: list[str], association: str) -> dict[str, Any]:
    text, label_text = f"{title}\n{body}", " ".join(labels)
    matches = sorted((m for pattern in MONEY for m in pattern.finditer(text)), key=lambda m: m.start())
    for match in matches:
        reward_d, expense_d = distance(REWARD, text, match.start()), distance(EXPENSE, text, match.start())
        if reward_d is None or (expense_d is not None and expense_d <= reward_d):
            continue
        amount = float(match.group("amount").replace(",", ""))
        amount = int(amount) if amount.is_integer() else amount
        currency = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP"}.get(match.group("currency").upper(), match.group("currency").upper())
        start, end = max(0, match.start() - 100), min(len(text), match.end() + 100)
        return {"amount": amount, "currency": currency, "provenance": "stated" if association in MAINTAINERS else "unverified", "verified": False, "evidence": compact(text[start:end], 260)}
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
    labels = sorted(str(x.get("name")) for x in item.get("labels", []) if isinstance(x, dict) and x.get("name"))
    association = str(item.get("author_association") or "NONE").upper()
    reward = reward_metadata(title, body, labels, association)
    if reward["amount"] is None:
        return None
    text = f"{title}\n{body}"
    return {
        "id": f"github-{project.replace('/', '-')}-{number}", "source": "github", "source_url": url,
        "title": title, "project": project, "issue_number": number, "category": category(title, labels),
        "status": "unclear" if UNAVAILABLE.search(text) or QUESTION.search(text) else "open", "github_state": "open",
        "reward": reward, "difficulty": "unknown", "ai_assistability": "unknown", "deadline": None,
        "competition": {"attempts": None, "claims": None, "open_prs": None},
        "assignees": sorted(str(x.get("login")) for x in item.get("assignees", []) if isinstance(x, dict) and x.get("login")),
        "labels": labels, "author_association": association, "body_excerpt": compact(body),
        "published_at": item.get("created_at"), "updated_at": item.get("updated_at"), "last_checked_at": checked_at,
        "discovery_sources": [source_id], "notes": None,
    }


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
    for source in sources:
        try:
            records = [r for item in client.search_issues(source["query"], source["max_pages"]) if (r := normalize_issue(item, source["id"], iso_z(now), now, stale, excluded))]
            fresh.extend(records)
            successful.add(source["id"])
            print(f"{source['id']}: kept {len(records)} candidates", file=sys.stderr)
        except SourceFetchError as exc:
            failed.append(source["id"])
            print(f"warning: {source['id']} failed: {exc}", file=sys.stderr)
    if not successful:
        raise CollectorError("all sources failed; existing output was left unchanged" if existing else "all sources failed; no output was written")
    preserved = [old for old in existing if not old.get("discovery_sources") or set(old.get("discovery_sources", [])) & set(failed)]
    output = merge_records([*fresh, *preserved])
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
