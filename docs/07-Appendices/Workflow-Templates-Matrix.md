# Workflow Templates Matrix

This matrix summarizes currently available workflow templates and their phase compositions.

## Template Matrix

| Template          | Primary Goal                            | Typical Phase Flow                                                                |
| ----------------- | --------------------------------------- | --------------------------------------------------------------------------------- |
| `blog_post`       | Generate publication-ready blog content | `research -> draft -> assess -> refine -> finalize -> image_selection -> publish` |
| `social_media`    | Create social-ready post content        | `research -> draft -> assess -> finalize -> publish`                              |
| `email`           | Create campaign email content           | `draft -> assess -> finalize -> publish`                                          |
| `newsletter`      | Build newsletter content package        | `research -> draft -> assess -> refine -> finalize -> image_selection -> publish` |
| `market_analysis` | Produce market analysis report          | `research -> assess -> analyze -> report -> publish`                              |

## Notes

- Template definitions are sourced from `TemplateExecutionService.get_template_definitions()`.
- Template execution is handled through `POST /api/workflows/execute/{template_name}`.
- `skip_phases` and `quality_threshold` can be supplied at execution time.

## Source Files

- `src/cofounder_agent/services/template_execution_service.py`
- `src/cofounder_agent/routes/workflow_routes.py`
- `src/cofounder_agent/services/workflow_executor.py`
