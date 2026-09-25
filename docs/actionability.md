# Actionability and refresh semantics

This note records the GitHub-only collector behavior hardened after reviewing real once-daily runs in Issue #14.

## Actionable means available to start

The first-stage classifier still requires a direct, positive maintainer-side reward offer and keeps the existing mirror/meta/contributor-payment protections.

A retained candidate then receives a bounded second-stage check:

- an **actual open pull request** can make an issue non-actionable when GitHub timeline evidence shows that the PR attaches work with a closing keyword such as `Fixes #N`;
- ordinary issue comments containing `Fixes #N`, or body text such as `PR submitted`, are hints rather than sufficient proof by themselves;
- claim/attempt comments alone do not suppress multi-claim bounties;
- ambiguous bounty-availability questions make the opportunity unclear unless a newer maintainer-side comment confirms that it is still active;
- an explicit deadline is used for automatic expiry only when the date/time includes a timezone form the collector understands;
- `funding-pending` offers are not actionable until the source changes to an actually funded/claimable state.

The default generated dataset continues to contain actionable opportunities rather than uncertain ones, so an `unclear` result is withheld from the default output.

## Search-window misses are revalidated

Discovery queries remain deliberately bounded. Each configured query currently reads at most two 100-item pages instead of relying only on the first global top-100 window.

Pagination alone is not treated as completeness. If an opportunity from the previous snapshot is missing from a successful search, the collector directly fetches that issue and re-runs the same first- and second-stage checks. This preserves legitimate opportunities that merely fell out of the updated-search window while removing records that were closed, assigned, expired, lost their reward/actionability signal, or gained active work.

If a source or the direct/lifecycle revalidation request fails, prior data is preserved rather than interpreting missing evidence as proof that an opportunity is open or closed.

## Freshness without timestamp-only Git churn

`data/opportunities.json` is a material snapshot. The collector compares records while ignoring `last_checked_at` and rewrites the tracked file only when opportunity data materially changes. `last_changed_at` remains part of that material snapshot and records when the row last changed meaningfully.

The authoritative time of every successful scan is emitted separately as run status (`checked_at`) together with the opportunity count, successful/failed sources, and whether a material change occurred. The scheduled workflow publishes that status in the GitHub Actions run summary and uploads it as a short-lived artifact. Therefore a successful no-op scan remains observable without creating a repository commit whose only change is timestamps.

A row's committed `last_checked_at` describes the check represented by the current material snapshot; the run status describes the newest completed scan.

## Cadence and scope

The scheduled cadence stays at once per day (`17 0 * * *`, 00:17 UTC). Issue #14 does not broaden source coverage. Source expansion should be evaluated after these actionability and refresh semantics have run cleanly in production.
