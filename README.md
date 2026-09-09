# Open Work Radar

**Open Work Radar** is an experimental public tool for finding **currently actionable, publicly listed paid opportunities** on the web.

It starts with OSS bounties and GitHub reward issues, but the long-term scope is broader: documentation, translation, design, research, paid feedback, hackathons, contests, micro-grants, and other outcome-based work that people can openly discover and apply for.

> Scan public sources → verify what is still actionable → normalize the data → show the opportunities that appear open now.

> [!IMPORTANT]
> **Open Work Radar finds bounty/reward opportunities; this repository is not itself a bounty program.** Issues in this repository describe development work on the radar and do **not** carry a reward unless an issue explicitly says otherwise. This note applies to humans and automated agents alike.

## Status

🚧 **Early MVP.**

The first implementation milestone is a GitHub-only collector that can be run locally or through a manual GitHub Actions workflow. Scheduled collection remains intentionally disabled until the manual workflow has been validated.

Nothing in this repository should currently be treated as a complete opportunity index.

## v0.1a GitHub collector

The collector reads the small query set in [`sources.yaml`](./sources.yaml), fetches recent open GitHub Issues, applies conservative local filtering, deduplicates overlapping results, and writes normalized records to [`data/opportunities.json`](./data/opportunities.json).

v0.1a deliberately favors **precision over recall**:

- closed and stale Issues are excluded;
- this repository excludes itself from collection;
- a reward-like label or word without an explicit amount is not enough to enter the dataset;
- maintainer-authored reward statements are distinguished from third-party claims using GitHub `author_association`;
- a cost, fee, credit purchase, deposit, or similar contributor expense is not treated as the reward amount;
- explicit uncertainty such as “is this bounty still available?” or a source that says the work is unavailable produces `status: unclear` rather than `open`;
- fields such as difficulty and AI assistability remain `unknown` when v0.1a cannot support them reliably.

Run locally with Python 3.10+:

```bash
python -m pip install -r requirements.txt
GITHUB_TOKEN=... python scripts/fetch_github.py
```

`GITHUB_TOKEN` is optional for public data but recommended because GitHub applies lower unauthenticated rate limits.

The GitHub Actions workflow can be started manually with `workflow_dispatch`. It commits only `data/opportunities.json` when generated output actually changes. **No cron schedule is enabled yet.**

## What it aims to show

For each opportunity, the radar should eventually surface useful, source-backed information such as:

- title and project
- category
- reward and currency
- reward provenance / confidence
- difficulty
- AI assistability
- deadline
- competition signals when available
- current status
- original source
- last checked time

Difficulty is about the **task itself**, not about whether it suits a particular person.

## Planned scope

### v0.1 — GitHub first

Discover and validate open GitHub issues with signals such as:

- `bounty`
- `reward`
- `funded`
- `good first issue`
- `documentation`
- `translation`

The goal is not to collect the largest possible list. The goal is to answer:

> **What public GitHub bounty or reward opportunities appear to be genuinely open right now?**

### Later

Potential sources include:

- Algora
- Opire
- IssueHunt, if still operational and machine-accessible
- Superteam Earn
- Dework
- Devpost
- Kaggle
- independent OSS bounty programs
- public micro-grant and paid-feedback programs

Sources will only be added when automated collection is technically and legally reasonable.

## Automation

The intended default is periodic collection with **GitHub Actions**, but scheduling is not enabled in v0.1a.

A scan roughly:

1. reads configured sources;
2. fetches current candidates;
3. filters and normalizes them into a common schema;
4. checks GitHub open/stale state and explicit availability signals;
5. preserves reward provenance;
6. keeps previous records from any source that failed during the current run;
7. updates generated data only after at least one source succeeds.

One broken source should not erase or invalidate the rest of the radar.

## What this project is not

Open Work Radar is a **discovery and evaluation tool**, not an automated application bot.

It should not, by default:

- mass-post bounty claims or `/attempt` comments;
- open speculative PRs at scale;
- evade rate limits or bot protections;
- present unverified reward claims as guaranteed payouts;
- store a maintainer's private application history, income, payout details, or personal scoring.

Personal participation records belong in a separate private repository. This project is intended to stay useful to anyone.

## Design

See [DESIGN.md](./DESIGN.md) for the current product principles, proposed data model, automation plan, difficulty model, reward verification levels, and MVP milestones.

## Contributing

Contributions around source discovery, normalization, stale detection, reward verification, classification, and presentation are welcome.

If you know of a public source of outcome-based paid work that could be collected responsibly, opening an Issue is a useful way to suggest it.

## Origin

Open Work Radar began with a simple question:

> Instead of manually hunting for OSS bounties and other small public jobs, could a lightweight open tool keep watch and show what is actually available now?

That is the experiment.
