# Parked dashboards

Dashboards in this directory are **not provisioned** — the Grafana file
provider watches only `infrastructure/grafana/dashboards/` (mounted at
`/etc/grafana/dashboards`), so anything here is invisible to Grafana until
moved back.

Parking is the middle ground between "13 boards, several empty" and
deleting work we'll want later (`feedback_deletion_criteria`: eligibility
is whether it's still wanted, and these are — just not yet).

## Currently parked

| Dashboard      | Parked                           | Why                                                                                                                                         | Unpark when                                                                                                                                                    |
| -------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `revenue.json` | 2026-07-01 (observability audit) | `revenue_events` holds exactly 1 row (2026-04-25). Twelve permanently-"No data" panels train the operator to distrust dashboards generally. | Monetization goes live and `revenue_events` receives real writes (Lemon Squeezy webhook → revenue engine). `git mv` it back — the provider reloads within 30s. |

## How to unpark

```bash
git mv infrastructure/grafana/dashboards-parked/<name>.json \
       infrastructure/grafana/dashboards/<name>.json
# commit + deploy-clone sync; the file provider picks it up in ~30s
```
