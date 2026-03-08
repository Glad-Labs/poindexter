# OAuth Integration

OAuth support is unified in a single auth router with CSRF state validation for GitHub callback security.

## Primary Endpoints

- `POST /api/auth/github/callback`
- `POST /api/auth/github-callback` (fallback/deprecated path)
- `POST /api/auth/logout`

## Security Controls

- CSRF state generation and one-time validation
- State expiration window enforcement
- Callback validation for missing/invalid state and code parameters

## Key Implementation Files

- `src/cofounder_agent/routes/auth_unified.py`
- `src/cofounder_agent/services/token_manager.py`
- `src/cofounder_agent/services/token_validator.py`

## Notes

- `auth_unified.py` stores state tokens with expiration and removes them after successful validation.
- OAuth callback processing includes token exchange and secure token storage workflow.
