"""Get a Google OAuth refresh_token for Singer taps (GSC + GA4).

Run once after creating an OAuth client in Google Cloud Console.
Outputs a refresh_token that you paste into Poindexter via
``poindexter settings set google_oauth_refresh_token <value> --secret``
(and the same with client_id / client_secret).

Usage:

    pip install google-auth-oauthlib
    python scripts/google-oauth-setup.py \\
        --client-id <YOUR_CLIENT_ID> \\
        --client-secret <YOUR_CLIENT_SECRET>

The script opens a browser, you sign in to your Google account, click
allow on the consent screen, and the script captures the auth code via
a local HTTP loopback (no copy-paste needed). It then exchanges the
code for a refresh_token and prints it.

Scopes requested:
  https://www.googleapis.com/auth/webmasters.readonly  (GSC)
  https://www.googleapis.com/auth/analytics.readonly   (GA4)

Both Singer taps (tap-google-search-console + tap-ga4) use the same
OAuth client + refresh_token, so you only do this once.
"""

from __future__ import annotations

import argparse
import json
import sys


SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Google OAuth helper for Singer taps")
    parser.add_argument("--client-id", required=True, help="OAuth 2.0 Client ID from Google Cloud Console")
    parser.add_argument("--client-secret", required=True, help="OAuth 2.0 Client Secret")
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Local port for the OAuth redirect (default 8765). Must match the redirect_uri "
             "registered in your OAuth client."
    )
    args = parser.parse_args()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "ERROR: google-auth-oauthlib not installed. Run:\n"
            "    pip install google-auth-oauthlib\n",
            file=sys.stderr,
        )
        return 1

    client_config = {
        "installed": {
            "client_id": args.client_id,
            "client_secret": args.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"http://localhost:{args.port}/"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    print(f"Opening browser for OAuth (port {args.port})...")
    print("Sign in with the Google account that has access to your GSC + GA4 properties.")
    print()
    creds = flow.run_local_server(port=args.port, prompt="consent", access_type="offline")

    if not creds.refresh_token:
        print(
            "ERROR: did not receive a refresh_token. Re-run with a clean OAuth grant — "
            "Google only issues a refresh_token on the FIRST consent. Revoke the app at "
            "https://myaccount.google.com/permissions and try again.",
            file=sys.stderr,
        )
        return 2

    print()
    print("=" * 70)
    print("SUCCESS — paste these into Poindexter:")
    print("=" * 70)
    print()
    print(f"  poindexter settings set google_oauth_client_id {args.client_id}")
    print(f"  poindexter settings set google_oauth_client_secret '{args.client_secret}' --secret")
    print(f"  poindexter settings set google_oauth_refresh_token '{creds.refresh_token}' --secret")
    print()
    print("(Or as a JSON blob for the tap_config:)")
    print()
    print(json.dumps({
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "refresh_token": creds.refresh_token,
    }, indent=2))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
