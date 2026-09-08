# Open Work Radar

**Open Work Radar** is an experimental public tool for finding **currently actionable, publicly listed paid opportunities** on the web.

It starts with OSS bounties and GitHub reward issues, but the long-term scope is broader: documentation, translation, design, research, paid feedback, hackathons, contests, micro-grants, and other outcome-based work that people can openly discover and apply for.

> Scan public sources → verify what is still actionable → normalize the data → show the opportunities that appear open now.

## Status

🚧 **Early design / pre-MVP.**

The repository currently contains the product design. The first implementation milestone is a GitHub-first radar that can run periodically with GitHub Actions and generate a current list of open bounty/reward opportunities.

Nothing in this repository should currently be treated as a live or complete opportunity index.

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

The intended default is periodic collection with **GitHub Actions**.

A future scan will roughly:

1. read configured sources;
2. fetch current opportunities;
3. normalize them into a common schema;
4. verify open/closed/stale status;
5. classify basic metadata such as difficulty;
6. preserve reward provenance;
7. archive expired or closed items;
8. update generated data and the public summary.

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

The project is still at the design stage. Once the first collector exists, contributions around source discovery, normalization, stale detection, reward verification, classification, and presentation will be welcome.

If you know of a public source of outcome-based paid work that could be collected responsibly, opening an Issue will be a useful way to suggest it once the project workflow is in place.

## Origin

Open Work Radar began with a simple question:

> Instead of manually hunting for OSS bounties and other small public jobs, could a lightweight open tool keep watch and show what is actually available now?

That is the experiment.
