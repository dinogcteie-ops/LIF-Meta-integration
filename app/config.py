from functools import lru_cache
from pathlib import Path

import bcrypt as _bcrypt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_password: str = "changeme"
    app_password_hash: str = ""
    secret_key: str = "dev-secret-change-me"
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
    meta_verify_token: str = ""        # webhook subscription verify token
    meta_page_access_token: str = ""   # long-lived Page token (lead retrieval)
    meta_ad_account_id: str = ""       # numeric act id, without the "act_" prefix
    meta_graph_version: str = "v19.0"

    def password_hash(self) -> str:
        if self.app_password_hash:
            return self.app_password_hash
        return _bcrypt.hashpw(self.app_password.encode(), _bcrypt.gensalt()).decode()


@lru_cache
def get_settings() -> Settings:
    return Settings()


BASE_DIR = Path(__file__).resolve().parent
