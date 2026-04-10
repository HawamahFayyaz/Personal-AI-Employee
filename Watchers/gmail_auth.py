"""
Gmail OAuth2 authentication helper.

Usage:
    from Watchers.gmail_auth import get_gmail_service

    service = get_gmail_service()
    # service is a googleapiclient.discovery Resource ready to use
"""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Paths are relative to the vault root (one level up from this file)
_VAULT_ROOT = Path(__file__).parent.parent
_CREDENTIALS_PATH = _VAULT_ROOT / "credentials.json"
_TOKEN_PATH = _VAULT_ROOT / "token.json"


def get_gmail_service():
    """Return an authenticated Gmail API service object.

    On the first run this opens a browser for the OAuth2 consent screen and
    writes token.json to the vault root.  Subsequent calls load and (if
    necessary) silently refresh the stored token.

    Returns:
        googleapiclient.discovery.Resource: Authenticated Gmail service.

    Raises:
        FileNotFoundError: If credentials.json is missing from the vault root.
    """
    if not _CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"credentials.json not found at {_CREDENTIALS_PATH}. "
            "Download it from the Google Cloud Console and place it in the vault root."
        )

    creds = None

    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CREDENTIALS_PATH), SCOPES
            )
            # Manual OAuth flow for WSL/headless environments
            flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
            auth_url, _ = flow.authorization_url(prompt='consent')
            print(f'\nPlease visit this URL to authorize:\n{auth_url}\n')
            code = input('Enter the authorization code: ')
            flow.fetch_token(code=code)
            creds = flow.credentials

        _TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)