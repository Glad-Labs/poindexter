---
name: cost-report
description: Show AI/LLM spending, budget usage, and operational cost metrics. Use when the user says "how much have we spent", "cost report", "show costs", "budget status", or "spending breakdown".
---

# Cost Report

Shows Glad Labs pipeline spending and budget consumption by combining two API endpoints:

- `GET /api/metrics/costs/budget` — daily/monthly spend vs. configured limits
- `GET /api/metrics/operational` — task counts and throughput metrics that indirectly reflect cost

Costs are primarily electricity for local GPU inference (Ollama + SDXL on RTX 5090).
Per-token "cost" is calculated from GPU power draw × duration × `electricity_rate_kwh`
(currently $0.2552/kWh, Rhode Island residential).

## Usage

```bash
scripts/run.sh                # Full report (budget + operational)
scripts/run.sh budget         # Budget status only
scripts/run.sh operational    # Operational metrics only
```

## Parameters

None.

## Output

- **Budget**: month-to-date spend, monthly limit, daily spend, daily limit, headroom left.
- **Operational**: pending / in_progress / awaiting_approval / failed task counts, worker running status, last-run timestamps.

The two endpoints are independent — budget shows money, operational shows throughput. Read them together to answer "are we on track this month and is the worker actually doing anything".
