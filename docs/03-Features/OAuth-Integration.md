# OAuth Integration

OAuth support is unified in a single auth router with CSRF state validation for GitHub callback security.

## Primary Endpoints

- `POST /api/auth/github/callback` - GitHub OAuth callback
- `POST /api/auth/github-callback` - Fallback/legacy path
- `POST /api/auth/logout` - Logout and invalidate token
- `GET /api/auth/user` - Get current user information

## Security Controls

- CSRF state generation and one-time validation
- State expiration window enforcement (default: 10 minutes)
- Callback validation for missing/invalid state and code parameters
- Secure token refresh with max-age validation

## Request/Response Examples

### GitHub OAuth Callback

**Frontend initiates OAuth flow:**

```javascript
// Generate state token
const state = generateRandomString(32);
localStorage.setItem('oauth_state', state);

// Redirect to GitHub
const clientId = 'YOUR_GITHUB_CLIENT_ID';
const redirectUri = encodeURIComponent('http://localhost:3001/auth/callback');
window.location.href =
  `https://github.com/login/oauth/authorize?` +
  `client_id=${clientId}&` +
  `redirect_uri=${redirectUri}&` +
  `scope=user:email&` +
  `state=${state}`;
```

**Backend receives callback:**

```bash
curl -X POST http://localhost:8000/api/auth/github/callback \
  -H "Content-Type: application/json" \
  -d '{
    "code": "abc123def456...",
    "state": "randomstate123"
  }'
```

**Successful Response:**

```json
{
  "status": "success",
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "Bearer",
  "expires_in": 86400,
  "user": {
    "id": "user-123",
    "github_login": "octocat",
    "email": "user@example.com",
    "avatar_url": "https://avatars.githubusercontent.com/u/1?",
    "created_at": "2026-03-08T14:40:00Z"
  }
}
```

**Error Response (Invalid State):**

```json
{
  "status": "error",
  "error": "invalid_state",
  "message": "State token validation failed or expired",
  "error_code": 400
}
```

### Get Current User

**Request:**

```bash
curl -X GET http://localhost:8000/api/auth/user \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```

**Response:**

```json
{
  "id": "user-123",
  "github_login": "octocat",
  "email": "user@example.com",
  "avatar_url": "https://avatars.githubusercontent.com/u/1?",
  "created_at": "2026-03-08T14:40:00Z",
  "last_login": "2026-03-08T15:20:00Z"
}
```

### Logout

**Request:**

```bash
curl -X POST http://localhost:8000/api/auth/logout \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```

**Response:**

```json
{
  "status": "success",
  "message": "Logged out successfully"
}
```

## Key Implementation Files

- [src/cofounder_agent/routes/auth_unified.py](../../src/cofounder_agent/routes/auth_unified.py) - OAuth routes
- [src/cofounder_agent/services/token_manager.py](../../src/cofounder_agent/services/token_manager.py) - Token lifecycle
- [src/cofounder_agent/services/token_validator.py](../../src/cofounder_agent/services/token_validator.py) - Token validation
- [web/oversight-hub/src/lib/authClient.js](../../web/oversight-hub/src/lib/authClient.js) - Frontend OAuth client

## Notes

- State tokens are one-time use and automatically removed after validation
- Access tokens are JWT-based with configurable expiration
- Development mode supports dev-token for local testing (bypasses OAuth)
- All tokens include user_id claim for authorization
- Refresh tokens can extend session without re-authentication
