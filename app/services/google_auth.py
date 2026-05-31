import base64
import json

from google.oauth2.service_account import Credentials

from app.config import get_settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleAuthError(Exception):
    pass


def load_google_credentials() -> Credentials:
    s = get_settings()
    if s.google_sa_json_base64:
        info = json.loads(base64.b64decode(s.google_sa_json_base64))
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    if s.google_sa_json_path:
        return Credentials.from_service_account_file(s.google_sa_json_path, scopes=SCOPES)
    raise GoogleAuthError(
        "Google credentials not configured. Set GOOGLE_SA_JSON_PATH or GOOGLE_SA_JSON_BASE64 in .env."
    )
