# Open Work Radar — Design

Status: initial design / 2026-09-08

## Purpose

Open Work Radar is a public, general-purpose tool for discovering currently actionable, publicly listed paid opportunities on the web.

The project starts from OSS bounties, but is intentionally broader than bounty platforms. It may include:

- OSS bounties
- documentation / translation rewards
- design and content bounties
- research tasks
- paid product feedback
- hackathons
- contests
- micro-grants
- other public, outcome-based paid opportunities

The core idea is simple:

> Periodically scan public sources, normalize opportunities into one format, filter out stale or unavailable items, and show people what they can apply for now.

This repository is for everyone. It must not contain the maintainer's personal application history, private notes, income records, payout details, or individualized suitability scoring. Personal participation logs belong in a separate private repository.

---

## Product principles

1. **Public and general-purpose**
   - Do not optimize the data model around one person.
   - Anyone should be able to browse and use the radar.

2. **Actionability over volume**
   - Prefer a smaller list of genuinely open opportunities over a huge noisy index.
   - An item that is technically visible but already closed, stale, unclaimable, duplicated, or effectively abandoned should be downgraded or excluded.

3. **Source-backed status**
   - Preserve the original source URL.
   - Record when each item was last checked.
   - Distinguish verified facts from inferred metadata.

4. **Difficulty, not personal suitability**
   - Use general attributes such as difficulty, category, reward, deadline, competition, and AI-assistability.
   - Do not use a maintainer-specific score such as “Yamada fit.”

5. **Do not create spam**
   - The radar discovers work. It does not mass-apply, mass-comment, or mass-open PRs.
   - Respect platform rules, rate limits, robots policies, and project contribution norms.

6. **Reward claims need provenance**
   - A GitHub label containing “bounty” is not enough by itself.
   - Where possible, verify that a reward is backed by a recognized platform or explicit maintainer statement.
   - Suspicious, unbacked, or ambiguous rewards should be marked `unverified` rather than presented as guaranteed money.

---

## MVP scope

Start small.

### v0.1 — GitHub-first radar

Use GitHub APIs to discover open Issues with signals such as:

- `bounty`
- `reward`
- `funded`
- `good first issue`
- `documentation`
- `docs`
- `translation`
- combinations of these terms

For each candidate:

1. confirm the Issue is still open;
2. inspect labels and body;
3. capture reward information if explicit;
4. record assignee / attempt / claim / PR signals when available;
5. estimate difficulty from the task description and repository context;
6. store normalized data;
7. render the current list into README or generated data.

### v0.2 — Bounty aggregators

Add structured sources such as:

- Algora
- Opire
- IssueHunt, if still operational and machine-accessible

Prefer APIs or stable structured endpoints over scraping.

### v0.3 — Broader public work markets

Evaluate sources such as:

- Superteam Earn
- Dework
- Devpost
- Kaggle
- independent OSS bounty programs
- public micro-grant / paid-feedback programs

Only add a source when collection is technically and legally reasonable.

---

## Suggested repository structure

```text
open-work-radar/
├── README.md
├── DESIGN.md
├── sources.yaml
├── config.yaml
├── data/
│   ├── opportunities.json
│   └── archive.json
├── scripts/
│   ├── fetch_github.py
│   ├── normalize.py
│   ├── classify.py
│   └── render_readme.py
└── .github/
    └── workflows/
        └── radar.yml
```

This is a starting shape, not a fixed specification.

---

## Normalized opportunity model

Each opportunity should eventually support fields similar to:

```yaml
id: github-owner-repo-123
source: github
source_url: https://github.com/owner/repo/issues/123
title: Example task
project: owner/repo
category: docs
status: open
reward:
  amount: 100
  currency: USD
  verified: true
difficulty: easy
ai_assistability: high
deadline: null
competition:
  attempts: 2
  claims: null
  open_prs: 1
published_at: 2026-09-01T00:00:00Z
last_checked_at: 2026-09-08T00:00:00Z
notes: null
```

Not every source will provide every field. Missing data should remain unknown rather than be invented.

---

## Difficulty

Difficulty should describe the task itself, not the person viewing it.

Initial levels:

- `easy` — narrow scope, clear acceptance criteria, small docs/config/UI/simple-code change
- `medium` — requires repository understanding, moderate implementation, testing, or domain knowledge
- `hard` — broad architecture, specialized expertise, security-sensitive work, research-grade work, or substantial implementation
- `unknown` — insufficient information

Difficulty is inherently approximate. When machine-classified, it should be treated as an estimate.

---

## AI assistability

Keep this separate from difficulty.

Suggested values:

- `high` — well-specified code/docs tasks with reproducible checks
- `medium` — AI can accelerate work but human judgment or domain validation is important
- `low` — predominantly subjective, relationship-driven, physical-world, highly specialized, or difficult to validate automatically
- `unknown`

A hard task can still have high AI assistability, and an easy task can have low AI assistability.

---

## Opportunity status

Suggested public-facing status values:

- `open` — currently actionable
- `closing-soon` — actionable but near a known deadline
- `unclear` — source exists but eligibility / availability is ambiguous
- `closed` — no longer actionable
- `stale` — technically open but inactive beyond a configurable threshold

The default view should prioritize `open` and `closing-soon`.

---

## Reward verification

Suggested reward confidence:

- `verified` — reward explicitly backed by a known bounty platform, escrow mechanism, or clear maintainer-controlled payout record
- `stated` — maintainer states a reward but external backing is not independently confirmed
- `unverified` — reward-like wording exists but provenance is unclear

Never imply that payout is guaranteed merely because a reward is listed.

---

## Automation with GitHub Actions

The default implementation should use GitHub Actions for periodic scans.

Initial cadence: once or twice per day.

Example:

```yaml
on:
  schedule:
    - cron: "23 0,12 * * *"
  workflow_dispatch:
```

The workflow should:

1. check out the repository;
2. install dependencies;
3. read `sources.yaml`;
4. fetch current opportunities;
5. normalize and classify them;
6. archive closed / expired items;
7. update generated data and README;
8. commit only when generated output actually changed.

Avoid running at minute `00` if unnecessary, since scheduled GitHub Actions may be delayed during high-load periods.

The workflow must fail gracefully when an external source is unavailable. One broken source should not erase the rest of the radar.

---

## README output

The README should be useful without requiring any local setup.

A future generated summary may look like:

```text
Open opportunities: 47
New in 24h: 6
Last scan: 2026-09-08

| Difficulty | Type | Opportunity | Reward | Competition | Status | Checked |
|------------|------|-------------|--------|-------------|--------|---------|
| Easy       | Docs | ...         | $100   | Low         | Open   | 2h ago  |
```

Do not rank opportunities by a private user's preferences.

Later, a GitHub Pages frontend may add filters such as:

- Easy only
- Docs / Design / Dev / Research / Translation
- minimum reward
- deadline
- verified rewards only
- low-competition opportunities
- high AI-assistability

---

## Personal participation data

Out of scope for this public repository:

- which opportunities the maintainer applied to;
- personal success / failure history;
- hours worked;
- model or agent usage costs;
- private maintainer correspondence;
- payout account details;
- income and tax records;
- individualized opportunity scoring.

Those belong in a separate private repository.

Aggregated, anonymized lessons may later be contributed back here if useful to everyone.

---

## Safety and quality constraints

The project should explicitly avoid becoming an automated spam engine.

Do not build default behavior that:

- automatically claims every discovered bounty;
- automatically posts `/attempt` comments;
- opens speculative PRs at scale;
- circumvents platform rate limits or bot protections;
- scrapes sources that prohibit automated access;
- exposes API keys or private account data;
- treats cryptocurrency tokens or unverified promises as equivalent to cash.

Discovery and evaluation can be automated. Participation should remain a deliberate action by the user or a separately authorized agent workflow.

---

## First implementation milestone

A successful v0.1 should be able to run unattended in GitHub Actions and answer:

> “What public GitHub bounty / reward opportunities appear to be genuinely open right now?”

Minimum deliverables:

- `sources.yaml`
- GitHub fetcher
- normalized JSON output
- basic difficulty classification
- reward provenance flag
- stale / closed filtering
- generated README table
- scheduled GitHub Action
- manual `workflow_dispatch`

Do not add external marketplaces until this GitHub-only loop works reliably.

---

## Origin

The project was started after experimenting with an OSS bounty and asking whether public paid tasks could be discovered automatically rather than searched manually.

The design goal is not “find work for one specific person.” It is:

> Build an open radar for public, outcome-based work that humans can browse and, where appropriate, complete with AI assistance.
