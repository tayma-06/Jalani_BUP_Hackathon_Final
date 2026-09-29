"""Standard-library HTTP helpers for real simulator/application checks."""
import json
import time
import urllib.error
import urllib.request


def request(base, path, method="GET", body=None, token=None, timeout=8):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data, headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read().decode()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return response.status, dict(response.headers), payload


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def eventually(check, timeout=90, interval=1):
    """Only retry read-only checks. Never retry an admin mutation implicitly."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            return check()
        except (AssertionError, OSError, ValueError) as error:
            last_error = error
            time.sleep(interval)
    raise AssertionError(f"Readiness deadline exceeded: {last_error}")


def login(base, username, password):
    status, _, data = request(base, "/api/auth/login", "POST",
                              {"username": username, "password": password})
    require(status == 200, f"Login returned HTTP {status}")
    require(isinstance(data, dict) and isinstance(data.get("access_token"), str),
            "Login must return access_token")
    return data["access_token"]


class TokenCache:
    """Reuses one access token per account.

    A retry loop calls the same check repeatedly. Signing in on every attempt
    trips the application's sign-in rate limiter, so the reported error becomes
    HTTP 429 and hides the failure that actually started the retries.
    """

    def __init__(self):
        self._tokens = {}

    def token(self, base, username, password):
        key = (base.rstrip("/"), username)
        if key not in self._tokens:
            self._tokens[key] = login(base, username, password)
        return self._tokens[key]

    def forget(self, base, username):
        self._tokens.pop((base.rstrip("/"), username), None)


def code(data):
    if not isinstance(data, dict):
        return None
    for key in ("detail", "error"):
        if isinstance(data.get(key), dict):
            return data[key].get("code")
    return None
