# Parked dashboards

Dashboards in this directory are **not provisioned** — the Grafana file
provider watches only `infrastructure/grafana/dashboards/` (mounted at
`/etc/grafana/dashboards`), so anything here is invisible to Grafana until
moved back.

Parking is the middle ground between "13 boards, several empty" and
deleting work we'll want later (`feedback_deletion_criteria`: eligibility
is whether it's still wanted, and these are — just not yet).

## Currently parked

_Nothing is parked right now._

## Previously parked

| Dashboard      | Parked                           | Unparked   | Why it came back                                                                                                                                             |
| -------------- | -------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `revenue.json` | 2026-07-01 (observability audit) | 2026-09-22 | `revenue_events` now has a live producer: the invoice poll in `services/pro_delivery.py` writes every Lemon Squeezy charge, renewal and refund (stack#3216). |

## How to unpark

```bash
git mv infrastructure/grafana/dashboards-parked/<name>.json \
       infrastructure/grafana/dashboards/<name>.json
# commit + deploy-clone sync; the file provider picks it up in ~30s
```
