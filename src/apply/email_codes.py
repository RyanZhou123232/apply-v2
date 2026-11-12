"""Read employer email-verification codes from Gmail (read-only), only from mail newer than the submit click.

Token: OAuth authorized-user JSON (refresh_token, client_id, client_secret, token_uri) at
$APPLY_GMAIL_TOKEN_FILE (default /home/box/.secrets/apply-v2/gmail-token.json). Token contents are never logged.
"""
from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
DEFAULT_TOKEN_FILE = "/home/box/.secrets/apply-v2/gmail-token.json"
API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
CODE_WORDS = re.compile(r"verification code|security code|confirmation code|one[- ]time (pass)?code|your code|enter (the|this) code|code to (confirm|verify)", re.I)
_STOP = {"security", "verification", "application", "confirm", "greenhouse", "password", "submitted", "complete", "minutes", "copy", "paste"}


def _token_path() -> Path:
    return Path(os.environ.get("APPLY_GMAIL_TOKEN_FILE", DEFAULT_TOKEN_FILE))


def access_token() -> str:
    data = json.loads(_token_path().read_text(encoding="utf-8"))
    body = urllib.parse.urlencode({"client_id": data["client_id"], "client_secret": data["client_secret"],
                                   "refresh_token": data["refresh_token"], "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request(data.get("token_uri", "https://oauth2.googleapis.com/token"), data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["access_token"]


def _get(url: str, token: str, params: dict) -> dict:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params, doseq=True)}",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _body_text(payload: dict) -> str:
    out = []

    def walk(p):
        mt = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if data and mt in ("text/plain", "text/html"):
            t = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            if mt == "text/html":
                t = html.unescape(re.sub(r"<(style|script)[^>]*>.*?</\1>|<[^>]+>", " ", t, flags=re.S | re.I))
            out.append(t)
        for c in p.get("parts", []) or []:
            walk(c)
    walk(payload)
    return re.sub(r"[ \t\r\f\v]+", " ", "\n".join(out))


def extract_code(text: str, length: Optional[int] = None) -> Optional[str]:
    """Find a code token shortly after verification-code wording. Prefers tokens of the expected length."""
    cands = []
    for m in CODE_WORDS.finditer(text or ""):
        window = text[m.end(): m.end() + 300]
        for tok in re.findall(r"(?<![\w-])([A-Za-z0-9]{4,12})(?![\w-])", window):
            if tok.lower() in _STOP or (tok.isalpha() and tok.islower()) or (tok.isalpha() and tok.istitle()):
                continue
            if length and len(tok) != length:
                continue
            cands.append(tok)
            break
    return cands[0] if cands else None


def wait_for_code(since_epoch: float, length: Optional[int] = None, sender_hint: str = "",
                  timeout_s: int = 180, interval_s: int = 5, skew_s: int = 30) -> Optional[str]:
    """Poll Gmail for a code in a message received after since_epoch (minus a small clock-skew allowance;
    Gmail's receive time can be a moment *before* our local click time). Newest message wins."""
    deadline = time.time() + timeout_s
    token, token_at = None, 0.0
    seen: set[str] = set()
    q = f"after:{int(since_epoch) - 60} " + (f"({sender_hint} OR code) " if sender_hint else "") + "(code OR verification OR security)"
    while time.time() < deadline:
        try:
            if not token or time.time() - token_at > 1800:
                token, token_at = access_token(), time.time()
            listing = _get(API, token, {"q": q, "maxResults": 10})
            for item in listing.get("messages", []):
                if item["id"] in seen:
                    continue
                msg = _get(f"{API}/{item['id']}", token, {"format": "full"})
                seen.add(item["id"])
                if int(msg.get("internalDate", 0)) / 1000 < since_epoch - skew_s:  # never reuse an older code
                    continue
                headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
                text = headers.get("subject", "") + "\n" + _body_text(msg["payload"])
                code = extract_code(text, length)
                if code:
                    log.info("verification code found in message from %s", headers.get("from", "?"))
                    return code
        except Exception as e:  # network / auth hiccup: keep polling until the deadline
            log.warning("gmail poll failed: %s", str(e)[:200])
        time.sleep(interval_s)
    return None


def _body_links(payload: dict) -> list[str]:
    """All http(s) links in a message (href targets first, then bare URLs), HTML-unescaped."""
    links: list[str] = []

    def walk(p):
        mt = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if data and mt in ("text/plain", "text/html"):
            t = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            links.extend(html.unescape(u) for u in re.findall(r'href=["\'](https?://[^"\']+)', t, re.I))
            links.extend(html.unescape(u) for u in re.findall(r"https?://[^\s<>\"')\]]+", t))
        for c in p.get("parts", []) or []:
            walk(c)
    walk(payload)
    seen, out = set(), []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


VERIFY_LINK_RE = re.compile(r"verif|activat|confirm|validate|token=|/login\?|/account", re.I)


def pick_verification_link(links: list[str], host_hint: str = "") -> Optional[str]:
    """Prefer a verify/activate link on the employer's own host (e.g. acme.wd5.myworkdayjobs.com)."""
    host = (host_hint or "").lower()
    cands = [u for u in links if VERIFY_LINK_RE.search(u) and not re.search(r"unsubscribe|privacy|terms", u, re.I)]
    if host:
        on_host = [u for u in cands if host in u.lower()]
        if on_host:
            return on_host[0]
        return None
    return cands[0] if cands else None


def wait_for_link(since_epoch: float, host_hint: str = "", timeout_s: int = 240, interval_s: int = 6,
                  skew_s: int = 30) -> Optional[str]:
    """Poll Gmail for an account-verification link in mail received after since_epoch."""
    deadline = time.time() + timeout_s
    token, token_at = None, 0.0
    seen: set[str] = set()
    q = f"after:{int(since_epoch) - 60} (verify OR verification OR activate OR confirm OR account)"
    while time.time() < deadline:
        try:
            if not token or time.time() - token_at > 1800:
                token, token_at = access_token(), time.time()
            for item in _get(API, token, {"q": q, "maxResults": 10}).get("messages", []):
                if item["id"] in seen:
                    continue
                msg = _get(f"{API}/{item['id']}", token, {"format": "full"})
                seen.add(item["id"])
                if int(msg.get("internalDate", 0)) / 1000 < since_epoch - skew_s:
                    continue
                link = pick_verification_link(_body_links(msg["payload"]), host_hint)
                if link:
                    log.info("verification link found (host %s)", urllib.parse.urlparse(link).netloc)
                    return link
        except Exception as e:
            log.warning("gmail poll failed: %s", str(e)[:200])
        time.sleep(interval_s)
    return None
