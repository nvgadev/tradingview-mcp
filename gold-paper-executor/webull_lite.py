"""Webull OpenAPI client, stdlib only (no `requests`), so the launchd job needs no venv.

Signing is the same scheme as ~/Library/Application Support/tjl-python/webull_client.py
(verified there byte-for-byte against Webull's own SDK).  Credentials are read from
~/.config/webull/openapi.env and are never printed or logged.
"""
import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

CONFIG = os.path.expanduser("~/.config/webull/openapi.env")
SANDBOX_BASE = "https://api.sandbox.webull.com"


class WebullError(RuntimeError):
    pass


def load_creds(path=CONFIG):
    if not os.path.exists(path):
        raise WebullError(f"no credentials file at {path}")
    if oct(os.stat(path).st_mode)[-3:] not in ("600", "400"):
        raise WebullError(f"{path} must be chmod 600")
    out = {}
    for ln in open(path):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    if not out.get("WEBULL_APP_KEY") or not out.get("WEBULL_APP_SECRET"):
        raise WebullError("WEBULL_APP_KEY / WEBULL_APP_SECRET missing")
    return out


class Webull:
    def __init__(self, creds=None, base=SANDBOX_BASE, timeout=15):
        c = creds or load_creds()
        self._key, self._secret = c["WEBULL_APP_KEY"], c["WEBULL_APP_SECRET"]
        self.base, self.timeout = base, timeout
        self.host = base.split("//", 1)[1]

    @property
    def is_sandbox(self):
        return self.base == SANDBOX_BASE

    def _sign(self, uri, queries, body):
        h = {
            "x-app-key": self._key,
            "x-timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "x-signature-version": "1.0",
            "x-signature-algorithm": "HMAC-SHA1",
            "x-signature-nonce": str(uuid.uuid4()),
        }
        params = dict(h)
        params["host"] = self.host
        for k, v in (queries or {}).items():
            k = k.lower()
            params[k] = f"{params[k]}&{v}" if k in params else str(v)
        sts = f"{uri}&" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if body is not None:
            raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            sts += "&" + hashlib.md5(raw.encode()).hexdigest().upper()
        canonical = urllib.parse.quote(sts, safe="")
        sig = base64.b64encode(hmac.new((self._secret + "&").encode(), canonical.encode(),
                                        hashlib.sha1).digest()).decode()
        h["x-signature"] = sig
        return h

    def request(self, method, uri, queries=None, body=None):
        headers = self._sign(uri, queries, body)
        headers["x-version"] = "v1"          # sent, not signed
        headers["Content-Type"] = "application/json"
        url = self.base + uri
        if queries:
            url += "?" + urllib.parse.urlencode(queries)
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode() or "{}"
                code = r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read().decode(errors="replace"), e.code
        except Exception as e:                       # network trouble, DNS, timeout
            raise WebullError(f"{method} {uri} -> {type(e).__name__}") from None
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"_raw": raw[:300]}
        if code >= 400:
            raise WebullError(f"{method} {uri} -> HTTP {code}: {json.dumps(payload)[:300]}")
        return payload

    def get(self, uri, **q):
        return self.request("GET", uri, queries=q or None)

    def post(self, uri, body):
        return self.request("POST", uri, body=body)
