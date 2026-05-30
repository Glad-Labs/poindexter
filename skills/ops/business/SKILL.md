---
name: business
description: >
  Business analysis prompts — financial impact, market analysis, and
  performance analysis for a given topic. Use when an operator-facing
  business module needs a structured first-pass analysis.
license: Apache-2.0
metadata:
  category: financial
  prompts:
    - key: task.business_financial_impact
      output_format: json
      description: 'Analyze financial impact of a topic -> revenue_impact, cost_impact, roi_estimate, recommendations — basic but functional default prompt'
    - key: task.business_market_analysis
      output_format: json
      description: 'Perform a market analysis for a topic -> market_size, competitors, opportunities, threats — basic but functional default prompt'
    - key: task.business_performance_analysis
      output_format: json
      description: 'Analyze performance metrics for a topic -> metrics, trends, benchmarks, recommendations — basic but functional default prompt'
---

# Business analysis skill

Default operator-facing business-analysis prompts. `UnifiedPromptManager`
resolves each template by `key` (a Langfuse production-label override
still wins over the bodies below).

Default prompts — basic but functional; production-quality prompt packs ship as a premium add-on.

## task.business_financial_impact

```text
Analyze the financial impact of: {topic}
Return JSON with keys: revenue_impact, cost_impact, roi_estimate, recommendations.
```

## task.business_market_analysis

```text
Perform a market analysis for: {topic}
Return JSON with keys: market_size, competitors, opportunities, threats.
```

## task.business_performance_analysis

```text
Analyze performance metrics for: {topic}
Return JSON with keys: metrics, trends, benchmarks, recommendations.
```
