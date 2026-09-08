#!/usr/bin/env python3
"""Collect open, reward-like GitHub issues into a normalized JSON dataset.

The collector deliberately uses only the GitHub Issues Search API.  It is
safe to run repeatedly: results are deduplicated, closed issues are ignored,
and a failed source cannot erase records previously obtained from that source.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised by the CLI environment
    raise SystemExit("PyYAML is required; install dependencies with `pip install -r requirements.txt`.") from exc


DEFAULT_API_URL = "https://api.github.com"
DEFAULT_USER_AGENT = "open-work-radar/0.1a"
DEFAULT_PER_PAGE = 100
DEFAULT_MAX_PAGES = 2
DEFAULT_STALE_AFTER_DAYS = 180
MAX_BODY_EXCERPT = 2_000

REWARD_TERMS = re.compile(r"\b(?:bounty|bounties|reward|rewards|funded|funding|paid|payment|prize)\b", re.I)
MONEY_PATTERNS = (
    # Word guards keep hexadecimal hashes such as ``97ca54895cad24`` from
    # being mistaken for a CAD amount while still accepting ``CAD24``.
    re.compile(
        r"(?<![A-Za-z0-9])(?P<currency>US\$|\$|USD|CAD|AUD|EUR|€|GBP|£|JPY)\s*"
        r"(?P<amount>\d[\d,]*(?:\.\d{1,2})?)(?![A-Za-z0-9])",
        re.I,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s*"
        r"(?P<currency>USD|CAD|AUD|EUR|GBP|JPY|€|£)(?![A-Za-z0-9])",
        re.I,
    ),
)
TOKEN_PATTERN = re.compile(r"\b(?:BTC|ETH|SOL|USDT|USDC|DOT|MATIC|SOLANA|BITCOIN|ETHEREUM)\b", re.I)


class CollectorError(RuntimeError):
    """A fatal collector configuration or output error."""


class SourceFetchError(CollectorError):
    """A single source could not be fetched."""


class SearchClient(Protocol):
    """The small interface needed by ``collect``; useful for deterministic tests."""

    def search_issues(self, query: str, max_pages: int = DEFAULT_MAX_PAGES) -> Iterable[dict[str, Any]]:
        ...


class GitHubClient:
    """Small urllib-based GitHub API client so the workflow has no SDK lock-in."""

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        token: str | None = None,
        timeout: int = 30,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = opener

    def _get_json(self, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.api_url}{path}?{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": DEFAULT_USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = Request(url, headers=headers, method="GET")
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read()
                status = getattr(response, "status", 200)
                response_headers = getattr(response, "headers", {})
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # pragma: no cover - defensive for unusual HTTPError objects
                pass
            reset = ""
            if getattr(exc, "headers", None):
                reset_value = exc.headers.get("X-RateLimit-Reset")
                if reset_value:
                    reset = f"; rate-limit reset epoch {reset_value}"
            raise SourceFetchError(f"GitHub API HTTP {exc.code} for {path}{reset}: {detail}") from exc
        except URLError as exc:
            raise SourceFetchError(f"GitHub API network error for {path}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise SourceFetchError(f"GitHub API timed out for {path}") from exc

        if status < 200 or status >= 300:
            raise SourceFetchError(f"GitHub API returned HTTP {status} for {path}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceFetchError(f"GitHub API returned invalid JSON for {path}") from exc
        if not isinstance(payload, dict):
            raise SourceFetchError(f"GitHub API returned an unexpected payload for {path}")
        # A successful GitHub response normally has these headers. Reading them
        # here makes rate-limit failures visible through the next request rather
        # than silently pretending a partial scan was complete.
        remaining = response_headers.get("X-RateLimit-Remaining") if response_headers else None
        if remaining == "0" and path == "/search/issues":
            reset = response_headers.get("X-RateLimit-Reset", "unknown")
            payload["_open_work_radar_rate_limit_warning"] = f"search rate limit exhausted; reset epoch {reset}"
        return payload

    def search_issues(self, query: str, max_pages: int = DEFAULT_MAX_PAGES) -> Iterable[dict[str, Any]]:
        """Yield all issues for a bounded number of search-result pages."""
        per_page = DEFAULT_PER_PAGE
        for page in range(1, max_pages + 1):
            payload = self._get_json(
                "/search/issues",
                {"q": query, "per_page": per_page, "page": page},
            )
            warning = payload.get("_open_work_radar_rate_limit_warning")
            if warning:
                print(f"warning: {warning}", file=sys.stderr)
            items = payload.get("items")
            if not isinstance(items, list):
                raise SourceFetchError(f"GitHub search response for {query!r} did not contain items")
            for item in items:
                if isinstance(item, dict):
                    yield item
            total_count = payload.get("total_count")
            if not items or (isinstance(total_count, int) and page * per_page >= total_count) or len(items) < per_page:
                break


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def isoformat_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def load_sources(path: Path) -> tuple[list[dict[str, Any]], int]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CollectorError(f"sources file not found: {path}") from exc
    except OSError as exc:
        raise CollectorError(f"could not read sources file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CollectorError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise CollectorError(f"{path} must contain a top-level 'sources' list")
    try:
        stale_after_days = int(raw.get("stale_after_days", DEFAULT_STALE_AFTER_DAYS))
    except (TypeError, ValueError) as exc:
        raise CollectorError("stale_after_days must be an integer") from exc
    if stale_after_days < 0:
        raise CollectorError("stale_after_days cannot be negative")

    sources: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, source in enumerate(raw["sources"], start=1):
        if not isinstance(source, dict):
            raise CollectorError(f"source #{index} must be a mapping")
        source_id = str(source.get("id", "")).strip()
        query = str(source.get("query", "")).strip()
        if not source_id or not query:
            raise CollectorError(f"source #{index} needs non-empty id and query")
        if source_id in seen_ids:
            raise CollectorError(f"duplicate source id: {source_id}")
        seen_ids.add(source_id)
        try:
            max_pages = int(source.get("max_pages", DEFAULT_MAX_PAGES))
        except (TypeError, ValueError) as exc:
            raise CollectorError(f"source {source_id}: max_pages must be an integer") from exc
        if max_pages < 1 or max_pages > 10:
            raise CollectorError(f"source {source_id}: max_pages must be between 1 and 10")
        sources.append({"id": source_id, "query": query, "max_pages": max_pages})
    if not sources:
        raise CollectorError(f"{path} contains no sources")
    return sources, stale_after_days


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectorError(f"existing output {path} is not readable JSON; refusing to overwrite it") from exc
    # Accept both the v0.1a list and a future envelope so recovery never drops data.
    if isinstance(raw, dict) and isinstance(raw.get("opportunities"), list):
        raw = raw["opportunities"]
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise CollectorError(f"existing output {path} must be a JSON list of opportunities")
    return [dict(item) for item in raw]


def truncate_text(value: str, limit: int = MAX_BODY_EXCERPT) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def repository_name(item: Mapping[str, Any]) -> str:
    repository = item.get("repository")
    if isinstance(repository, dict) and repository.get("full_name"):
        return str(repository["full_name"])
    repository_url = str(item.get("repository_url") or "")
    match = re.search(r"/repos/([^/]+/[^/]+)$", repository_url)
    if match:
        return match.group(1)
    html_url = str(item.get("html_url") or "")
    match = re.search(r"github\.com/([^/]+/[^/]+)/issues/\d+", html_url)
    return match.group(1) if match else "unknown/unknown"


def normalize_currency(value: str) -> str:
    value = value.upper()
    return {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP"}.get(value, value)


def numeric_amount(value: str) -> int | float | None:
    try:
        parsed = float(value.replace(",", ""))
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def reward_metadata(title: str, body: str, labels: list[str]) -> dict[str, Any]:
    text = f"{title}\n{body}"
    label_text = " ".join(labels)
    matches: list[re.Match[str]] = []
    for pattern in MONEY_PATTERNS:
        match = pattern.search(text)
        if match:
            matches.append(match)
    if matches:
        match = matches[0]
        currency = normalize_currency(match.group("currency"))
        amount = numeric_amount(match.group("amount"))
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        return {
            "amount": amount,
            "currency": currency,
            "provenance": "stated",
            "verified": False,
            "evidence": truncate_text(text[start:end], 260),
        }

    reward_match = REWARD_TERMS.search(text)
    label_reward = REWARD_TERMS.search(label_text)
    token_match = TOKEN_PATTERN.search(text)
    if token_match and (reward_match or label_reward):
        start = max(0, token_match.start() - 100)
        end = min(len(text), token_match.end() + 100)
        return {
            "amount": None,
            "currency": "token",
            "provenance": "unverified",
            "verified": False,
            "evidence": truncate_text(text[start:end], 260),
        }
    if reward_match:
        start = max(0, reward_match.start() - 100)
        end = min(len(text), reward_match.end() + 100)
        return {
            "amount": None,
            "currency": None,
            "provenance": "unverified",
            "verified": False,
            "evidence": truncate_text(text[start:end], 260),
        }
    if label_reward:
        # A label alone is deliberately not treated as evidence of payment.
        return {
            "amount": None,
            "currency": None,
            "provenance": "unverified",
            "verified": False,
            "evidence": f"label signal: {truncate_text(label_text, 160)}",
        }
    return {"amount": None, "currency": None, "provenance": "unknown", "verified": False, "evidence": None}


def classify_category(title: str, body: str, labels: list[str]) -> str:
    text = f"{title} {body} {' '.join(labels)}".lower()
    if any(term in text for term in ("translation", "localization", "i18n")):
        return "translation"
    if any(term in text for term in ("documentation", "docs", "readme", "tutorial")):
        return "docs"
    if any(term in text for term in ("design", "ux", "ui", "visual")):
        return "design"
    if any(term in text for term in ("security", "vulnerability", "cve", "audit")):
        return "security"
    return "development"


def classify_difficulty(title: str, body: str, labels: list[str]) -> str:
    text = f"{title} {body} {' '.join(labels)}".lower()
    hard_signals = ("security", "vulnerability", "cryptograph", "architecture", "breaking change", "migration", "research")
    easy_signals = ("good first issue", "first-timers-only", "beginner", "documentation", "docs", "translation", "help wanted")
    if any(signal in text for signal in hard_signals):
        return "hard"
    if any(signal in text for signal in easy_signals):
        return "easy"
    return "medium"


def classify_ai_assistability(difficulty: str, category: str) -> str:
    if category in {"docs", "translation"} or difficulty == "easy":
        return "high"
    if difficulty == "hard":
        return "medium"
    return "medium"


def is_stale(updated_at: Any, now: dt.datetime, stale_after_days: int) -> bool:
    if stale_after_days == 0:
        return False
    updated = parse_timestamp(updated_at)
    if updated is None:
        return False
    return now - updated > dt.timedelta(days=stale_after_days)


def normalize_issue(
    item: Mapping[str, Any],
    source_id: str,
    checked_at: str,
    now: dt.datetime,
    stale_after_days: int,
) -> dict[str, Any] | None:
    # Search queries request open issues, but re-check state and PR shape because
    # search results can change between pages and an issue search may include PRs.
    if str(item.get("state", "")).lower() != "open" or item.get("pull_request"):
        return None
    if is_stale(item.get("updated_at"), now, stale_after_days):
        return None
    source_url = str(item.get("html_url") or "").strip()
    number = item.get("number")
    if not source_url or not isinstance(number, int):
        return None
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "")
    labels = sorted({str(label.get("name", "")).strip() for label in item.get("labels", []) if isinstance(label, dict) and label.get("name")})
    assignees = sorted({str(user.get("login", "")).strip() for user in item.get("assignees", []) if isinstance(user, dict) and user.get("login")})
    project = repository_name(item)
    category = classify_category(title, body, labels)
    difficulty = classify_difficulty(title, body, labels)
    return {
        "id": f"github-{project.replace('/', '-')}-{number}",
        "source": "github",
        "source_url": source_url,
        "title": title,
        "project": project,
        "category": category,
        "status": "open",
        "reward": reward_metadata(title, body, labels),
        "difficulty": difficulty,
        "ai_assistability": classify_ai_assistability(difficulty, category),
        "deadline": None,
        "competition": {"attempts": None, "claims": None, "open_prs": None},
        "assignees": assignees,
        "labels": labels,
        "body_excerpt": truncate_text(body),
        "published_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "last_checked_at": checked_at,
        "discovery_sources": [source_id],
        "notes": None,
    }


def merge_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    for record in records:
        source_url = str(record.get("source_url") or "")
        if not source_url:
            continue
        if source_url not in by_url:
            by_url[source_url] = dict(record)
            by_url[source_url]["discovery_sources"] = sorted(set(record.get("discovery_sources") or []))
            continue
        current = by_url[source_url]
        sources = set(current.get("discovery_sources") or [])
        sources.update(record.get("discovery_sources") or [])
        # The latest successful observation wins for mutable fields, while the
        # query provenance from overlapping searches is retained.
        current.update(record)
        current["discovery_sources"] = sorted(sources)
    return sorted(by_url.values(), key=lambda record: str(record.get("source_url", "")))


def atomic_write_json(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False)
    temporary_path = Path(handle.name)
    try:
        with handle:
            json.dump(records, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, path)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise CollectorError(f"could not write {path}: {exc}") from exc


def collect(
    sources_path: Path,
    output_path: Path,
    client: SearchClient,
    now: dt.datetime | None = None,
) -> tuple[int, int, list[str]]:
    sources, stale_after_days = load_sources(sources_path)
    existing = load_existing(output_path)
    now = now or utc_now()
    checked_at = isoformat_z(now)

    fresh_records: list[dict[str, Any]] = []
    successful_ids: set[str] = set()
    failed: list[tuple[str, str]] = []
    for source in sources:
        source_id = source["id"]
        try:
            items = client.search_issues(source["query"], max_pages=source["max_pages"])
            source_records = [
                record
                for item in items
                if (record := normalize_issue(item, source_id, checked_at, now, stale_after_days)) is not None
            ]
            fresh_records.extend(source_records)
            successful_ids.add(source_id)
            print(f"{source_id}: collected {len(source_records)} open opportunities", file=sys.stderr)
        except SourceFetchError as exc:
            failed.append((source_id, str(exc)))
            print(f"warning: source {source_id} failed: {exc}", file=sys.stderr)

    if not successful_ids:
        if existing:
            raise CollectorError("all configured sources failed; existing output was left unchanged")
        raise CollectorError("all configured sources failed; no output was written")

    merged_fresh = merge_records(fresh_records)
    failed_ids = {source_id for source_id, _ in failed}
    preserved: list[dict[str, Any]] = []
    for old in existing:
        old_sources = set(old.get("discovery_sources") or [])
        # Unknown provenance is preserved conservatively. Records associated
        # with a failed source are also preserved until that source succeeds.
        if not old_sources or old_sources & failed_ids:
            preserved.append(old)
    output_records = merge_records([*merged_fresh, *preserved])
    atomic_write_json(output_path, output_records)

    if failed:
        print(
            "warning: preserved records from failed sources; the dataset was not silently erased",
            file=sys.stderr,
        )
    return len(output_records), len(successful_ids), [source_id for source_id, _ in failed]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path("sources.yaml"), help="YAML source configuration")
    parser.add_argument("--output", type=Path, default=Path("data/opportunities.json"), help="normalized JSON output")
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", DEFAULT_API_URL), help="GitHub API base URL")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    now = None
    if args.now:
        now = parse_timestamp(args.now)
        if now is None:
            print("error: --now must be an ISO-8601 timestamp", file=sys.stderr)
            return 2
    try:
        count, source_count, failed = collect(
            args.sources,
            args.output,
            GitHubClient(api_url=args.api_url, token=token, timeout=args.timeout),
            now=now,
        )
    except CollectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    suffix = f"; failed sources: {', '.join(failed)}" if failed else ""
    print(f"wrote {count} opportunities from {source_count} successful sources{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
