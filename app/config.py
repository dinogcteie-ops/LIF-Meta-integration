from functools import lru_cache
from pathlib import Path

import bcrypt as _bcrypt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_password: str = "changeme"
    app_password_hash: str = ""
    secret_key: str = "dev-secret-change-me"
    # Mark the session cookie Secure (HTTPS-only). True in production (set
    # COOKIE_SECURE=true on Render); default False so localhost login still works.
    cookie_secure: bool = False

    # --- Google sign-in (OAuth 2.0; free) ---
    # Create a Web OAuth client in Google Cloud Console and register BOTH
    # redirect URIs:  https://lifcrm.netlify.app/auth/google/callback  (prod)
    # and             http://localhost:8000/auth/google/callback        (dev).
    # Leave blank to hide the Google button (password login always works).
    google_client_id: str = ""
    google_client_secret: str = ""
    # Public base for the redirect URI in prod (behind the Netlify proxy the
    # request's own host would be the Render hostname). Blank = use request URL.
    google_redirect_base: str = ""
    # Local SQLite by default; Supabase Postgres in production. Use the pooled
    # connection string (…pooler.supabase.com:6543) for the running app.
    database_url: str = "sqlite:///./lif.db"
    currency: str = "INR"

    # --- Legacy Google Sheets (only used by the one-time migration script) ---
    google_sa_json_path: str = ""
    google_sa_json_base64: str = ""
    google_sheet_id: str = ""
    google_drive_folder_id: str = ""

    # --- Meta (Facebook/Instagram) Ads integration ---
    meta_app_secret: str = ""          # for X-Hub-Signature-256 verification
    meta_verify_token: str = ""        # webhook subscription verify token; also the
                                       # shared token for the /jobs/* cron endpoints
    meta_page_access_token: str = ""   # long-lived Page token (lead retrieval)
    meta_ad_account_id: str = ""       # numeric act id, without the "act_" prefix
    meta_graph_version: str = "v19.0"

    # --- Email (Gmail SMTP) — follow-up reminder digests ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465               # SSL
    smtp_user: str = ""                # full Gmail address (login + default From)
    smtp_password: str = ""            # Gmail *app password*, not the account password
    smtp_from: str = ""                # overrides the From address; blank = smtp_user
    public_base_url: str = "https://lifcrm.netlify.app"  # for links in emails
    # Gmail API transport (HTTPS) — Render blocks outbound SMTP, so production
    # sends via the Gmail API instead. Reuses google_client_id/secret; the
    # refresh token (gmail.send scope) is minted once via
    # scripts/get_gmail_refresh_token.py. Blank → fall back to SMTP (local dev).
    gmail_refresh_token: str = ""

    # --- Inbound lead capture (Google Sheet) — interim until Meta is fully live ---
    leads_intake_sheet_id: str = ""    # spreadsheet key of the inbound enquiries sheet
    leads_intake_tab: str = "Form Responses 1"

    @property
    def email_from(self) -> str:
        return self.smtp_from or self.smtp_user

    def password_hash(self) -> str:
        if self.app_password_hash:
            return self.app_password_hash
        return _bcrypt.hashpw(self.app_password.encode(), _bcrypt.gensalt()).decode()


@lru_cache
def get_settings() -> Settings:
    return Settings()


BASE_DIR = Path(__file__).resolve().parent
