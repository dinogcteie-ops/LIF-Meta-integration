import json
import uuid
from datetime import date

from google.auth.transport.requests import AuthorizedSession

from app.config import get_settings
from app.services.excel import build_workbook
from app.services.google_auth import GoogleAuthError, load_google_credentials
from app.database import SheetDB

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


class DriveError(Exception):
    pass


def push_workbook_to_drive(db: SheetDB) -> str:
    settings = get_settings()
    try:
        creds = load_google_credentials()
    except GoogleAuthError as e:
        raise DriveError(str(e)) from e

    authed = AuthorizedSession(creds)
    filename = f"lif-export-{date.today().isoformat()}.xlsx"
    metadata: dict[str, object] = {"name": filename, "mimeType": XLSX_MIME}
    if settings.google_drive_folder_id:
        metadata["parents"] = [settings.google_drive_folder_id]

    workbook = build_workbook(db)
    response = authed.post(
        f"{UPLOAD_URL}?uploadType=multipart&fields=id,name,webViewLink",
        data=_multipart_body(metadata, workbook),
        headers={"Content-Type": f"multipart/related; boundary={_boundary}"},
        timeout=60,
    )

    if response.status_code >= 400:
        raise DriveError(_error_message(response))

    payload = response.json()
    return payload.get("webViewLink") or f"https://drive.google.com/file/d/{payload['id']}/view"


_boundary = f"lif-export-{uuid.uuid4().hex}"


def _multipart_body(metadata: dict[str, object], content: bytes) -> bytes:
    meta = json.dumps(metadata).encode("utf-8")
    return b"".join([
        f"--{_boundary}\r\n".encode(),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        meta, b"\r\n",
        f"--{_boundary}\r\n".encode(),
        f"Content-Type: {XLSX_MIME}\r\n\r\n".encode(),
        content, b"\r\n",
        f"--{_boundary}--\r\n".encode(),
    ])


def _error_message(response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    return payload.get("error", {}).get("message") or response.text
