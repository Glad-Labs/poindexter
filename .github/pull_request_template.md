## Summary

<!-- What does this PR do? 1-3 bullet points. -->

## Related Issues

<!-- Link related issues: Fixes #123, Closes #456 -->

## Test Plan

- [ ] Unit tests added/updated
- [ ] Existing tests pass (`python -m pytest tests/unit/ -q`)
- [ ] Linting passes (`npm run lint`)
- [ ] Tested locally with `docker compose up -d`

## Checklist

- [ ] No hardcoded secrets or credentials
- [ ] SQL queries use parameterized placeholders ($1, $2)
- [ ] New settings use `app_settings` table (not env vars)
- [ ] Logging uses `get_logger(__name__)` (no print statements)
