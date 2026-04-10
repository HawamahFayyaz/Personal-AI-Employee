"""
twitter_auth.py — OAuth 2.0 PKCE flow for Twitter/X API v2.

Run once to authorize the app and save tokens to .twitter_token.json:
    python Watchers/twitter_auth.py

Required environment variables (or .env at vault root):
    TWITTER_CLIENT_ID       OAuth 2.0 Client ID from developer.twitter.com
    TWITTER_CLIENT_SECRET   Client Secret (confidential apps only; omit for public apps)
    TWITTER_REDIRECT_URI    Callback URI registered in the app (default: http://localhost:8765/callback)

Output:
    {vault_root}/.twitter_token.json  containing access_token, refresh_token, expires_at
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Optional dotenv
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

VAULT_ROOT    = Path(__file__).parent.parent
TOKEN_PATH    = VAULT_ROOT / ".twitter_token.json"
AUTH_BASE     = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL     = "https://api.twitter.com/2/oauth2/token"
REVOKE_URL    = "https://api.twitter.com/2/oauth2/revoke"

SCOPES = "tweet.read tweet.write users.read dm.read offline.access"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Callback server — catches the ?code= redirect
# ---------------------------------------------------------------------------

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.__class__.code  = params.get("code", [None])[0]
        self.__class__.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<h2>Authorization successful! You can close this tab.</h2>"
        )

    def log_message(self, *args):  # silence httpd logs
        pass


# ---------------------------------------------------------------------------
# Token exchange + save
# ---------------------------------------------------------------------------

def _exchange_code(
    code: str,
    verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    payload = {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "code_verifier": verifier,
        "client_id":     client_id,
    }
    data    = urlencode(payload).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    if client_secret:
        creds   = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"

    req = Request(TOKEN_URL, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def save_tokens(token_data: dict) -> None:
    """Persist tokens to .twitter_token.json, adding expires_at."""
    expires_in = int(token_data.get("expires_in", 7200))
    token_data["expires_at"] = int(time.time()) + expires_in - 60  # 60-s safety margin
    TOKEN_PATH.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    print(f"Tokens saved → {TOKEN_PATH}")


def load_tokens() -> dict:
    """Load tokens from .twitter_token.json (returns empty dict if missing)."""
    if not TOKEN_PATH.exists():
        return {}
    try:
        return json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def refresh_tokens(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token."""
    payload = {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     client_id,
    }
    data    = urlencode(payload).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    if client_secret:
        creds   = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"

    req = Request(TOKEN_URL, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Main auth flow
# ---------------------------------------------------------------------------

def run_auth_flow() -> None:
    client_id     = os.getenv("TWITTER_CLIENT_ID", "")
    client_secret = os.getenv("TWITTER_CLIENT_SECRET", "")
    redirect_uri  = os.getenv("TWITTER_REDIRECT_URI", "http://localhost:8765/callback")

    if not client_id:
        print("[ERROR] TWITTER_CLIENT_ID is not set. Add it to your .env file.")
        sys.exit(1)

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    # Parse redirect_uri to get the port
    parsed_redirect = urllib.parse.urlparse(redirect_uri)
    port = parsed_redirect.port or 8765

    auth_params = {
        "response_type":         "code",
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "scope":                 SCOPES,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{AUTH_BASE}?{urlencode(auth_params)}"

    print("\n" + "=" * 60)
    print("Twitter/X OAuth 2.0 Authorization")
    print("=" * 60)
    print(f"Scopes requested: {SCOPES}")
    print(f"Redirect URI    : {redirect_uri}")
    print()
    print("Opening browser for authorization…")
    print("If the browser doesn't open, visit:")
    print(f"  {auth_url}")
    print()

    webbrowser.open(auth_url)

    # Start a one-shot HTTP server on the callback port
    server = http.server.HTTPServer(("localhost", port), _CallbackHandler)
    print(f"Waiting for callback on port {port}…")
    server.handle_request()   # blocks until one request is received
    server.server_close()

    code  = _CallbackHandler.code
    rcvd  = _CallbackHandler.state

    if not code:
        print("[ERROR] No authorization code received. Did you cancel the flow?")
        sys.exit(1)
    if rcvd != state:
        print("[ERROR] State mismatch — possible CSRF. Aborting.")
        sys.exit(1)

    print("Authorization code received. Exchanging for tokens…")
    try:
        token_data = _exchange_code(code, verifier, client_id, client_secret, redirect_uri)
    except Exception as exc:
        print(f"[ERROR] Token exchange failed: {exc}")
        sys.exit(1)

    if "error" in token_data:
        print(f"[ERROR] {token_data}")
        sys.exit(1)

    save_tokens(token_data)

    print()
    print("=" * 60)
    print("Authorization complete!")
    print(f"Access token  : {token_data.get('access_token', '')[:20]}…")
    print(f"Refresh token : {'present' if token_data.get('refresh_token') else 'MISSING — re-auth required when token expires'}")
    print(f"Scopes        : {token_data.get('scope', 'unknown')}")
    print("=" * 60)
    print()

    if "offline.access" not in token_data.get("scope", ""):
        print("[WARN] offline.access not granted — automatic token refresh will not work.")
        print("       Re-run this script when the token expires (~2 hours).")


if __name__ == "__main__":
    run_auth_flow()
