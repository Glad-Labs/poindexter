# Integrating OAuth for Your App

**Goal:** Integrate Glad Labs OAuth flow in your app and securely call protected APIs.

**Time:** ~20 minutes  
**Difficulty:** Intermediate

---

## Overview

In this tutorial, you'll:

1. Initiate GitHub OAuth login
2. Handle callback and state validation
3. Store session tokens safely
4. Call protected endpoints

By the end, your app can authenticate users and execute workflows as signed-in users.

---

## Prerequisites

- Backend running (`http://localhost:8000`)
- GitHub OAuth app configured in environment variables
- Frontend app with callback route

Reference docs:

- [OAuth Integration Feature](../03-Features/OAuth-Integration.md)
- [Environment Variables](../01-Getting-Started/Environment-Variables.md)

---

## Step 1: Start OAuth Login

Redirect users to backend OAuth start route:

```bash
curl -i http://localhost:8000/api/auth/github/login
```

In a browser flow, this endpoint should redirect to GitHub authorization.

---

## Step 2: Handle Callback in Frontend

Your frontend callback route should read query parameters and pass them to backend callback endpoint.

Example callback URL pattern:

```text
http://localhost:3001/auth/callback?code=<github_code>&state=<oauth_state>
```

Backend callback endpoint:

```text
GET /api/auth/github/callback
```

Important checks:

- Validate `state` to prevent CSRF
- Enforce state expiry window
- Reject callbacks with missing code/state

---

## Step 3: Persist Auth Session

Use your existing auth client/service layer to persist authenticated session state.

Recommended practices:

- Centralize token/session access in one auth module
- Avoid duplicating callback validation logic across pages
- Clear session on logout and auth failures

---

## Step 4: Call Protected API

After successful auth, call a protected endpoint:

```bash
curl -X GET http://localhost:8000/api/auth/user \
  -H "Authorization: Bearer <token>"
```

Expected: user profile payload with `user_id`, `email`, and metadata.

---

## Troubleshooting

### `401 Unauthorized`

- Token missing or expired
- Authorization header format is wrong

### `Invalid state`

- Callback state does not match stored state
- State expired before callback completed

### Login redirect loop

- Callback route not storing session
- Frontend auth guard redirects before callback processing completes

See: [Troubleshooting Hub](../06-Troubleshooting/README.md)

---

## Next Steps

- Add route guards for authenticated pages.
- Add refresh/session-expiration UX handling.
- Continue with [Your First Workflow](Your-First-Workflow.md) using authenticated requests.
