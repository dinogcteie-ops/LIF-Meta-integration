"""Email transport selection: Gmail API (HTTPS) when configured, else SMTP.

Render blocks outbound SMTP, so prod must dispatch via the Gmail API. These
tests don't hit the network — requests.post is faked.
"""
from types import SimpleNamespace

import app.services.email as email


def _gmail_settings(**over):
    base = dict(
        gmail_refresh_token="refresh-tok",
        google_client_id="cid", google_client_secret="cs",
        smtp_user="", smtp_password="",
        smtp_host="smtp.gmail.com", smtp_port=465,
        email_from="studio@example.com",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeResp:
    def __init__(self, ok=True, status=200, payload=None, text=""):
        self.ok = ok
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.content = b"{}"

    def json(self):
        return self._payload


def _patch_gmail(monkeypatch, settings):
    monkeypatch.setattr(email, "get_settings", lambda: settings)
    email._token_cache["token"] = ""
    email._token_cache["exp"] = 0.0
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        if url == email._OAUTH_TOKEN_URL:
            return _FakeResp(payload={"access_token": "ACCESS", "expires_in": 3600})
        if url == email._GMAIL_SEND_URL:
            return _FakeResp(payload={"id": "msg-1"})
        return _FakeResp(ok=False, status=404, text="unexpected")

    monkeypatch.setattr(email.requests, "post", fake_post)
    return calls


def test_email_configured_true_with_gmail_only(monkeypatch):
    monkeypatch.setattr(email, "get_settings", lambda: _gmail_settings())
    assert email.email_configured() is True


def test_send_email_uses_gmail_api(monkeypatch):
    calls = _patch_gmail(monkeypatch, _gmail_settings())
    # SMTP must NOT be used — make it explode if dispatched there.
    monkeypatch.setattr(email, "_send_via_smtp",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("used SMTP")))

    email.send_email("Subject", "<b>hi</b>", ["owner@example.com"])

    urls = [u for u, _ in calls]
    assert email._OAUTH_TOKEN_URL in urls          # token refreshed
    assert email._GMAIL_SEND_URL in urls           # message sent via Gmail API
    send_call = next(kw for u, kw in calls if u == email._GMAIL_SEND_URL)
    assert send_call["headers"]["Authorization"] == "Bearer ACCESS"
    assert "raw" in send_call["json"]              # base64url RFC-822 payload


def test_send_email_with_images_uses_gmail_api(monkeypatch):
    calls = _patch_gmail(monkeypatch, _gmail_settings())
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16

    email.send_email_with_images("Report", "<img src='cid:c'>", {"c": png},
                                 ["owner@example.com"], text="plain")

    assert email._GMAIL_SEND_URL in [u for u, _ in calls]


def test_gmail_token_error_raises_emailerror(monkeypatch):
    monkeypatch.setattr(email, "get_settings", lambda: _gmail_settings())
    email._token_cache["token"] = ""
    email._token_cache["exp"] = 0.0

    def fake_post(url, **kw):
        return _FakeResp(ok=False, status=400, payload={"error": "invalid_grant"})

    monkeypatch.setattr(email.requests, "post", fake_post)
    try:
        email.send_email("S", "<b>h</b>", ["owner@example.com"])
        assert False, "expected EmailError"
    except email.EmailError as exc:
        assert "invalid_grant" in str(exc)
