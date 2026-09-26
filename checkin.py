"""GLaDOS check-in using credentials supplied only through environment variables."""
import os
import re
import sys
from decimal import Decimal, InvalidOperation

import requests

BASE = "https://glados.cloud"
# Match the browser used to obtain COOKIE; override when renewing from another device.
DEFAULT_USER_AGENT = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Edg/153.0.0.0 Mobile Safari/537.36"


class CheckinError(RuntimeError):
    pass


def number_text(value):
    try:
        return str(int(Decimal(str(value))))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return "unknown"


def api(session, method, path, **kwargs):
    try:
        response = session.request(method, BASE + path, timeout=30, allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise CheckinError(path + ": network request failed (credentials omitted)") from None
    print(path + ": HTTP " + str(response.status_code))
    if response.status_code != 200:
        raise CheckinError(path + ": unexpected HTTP status; check authentication or service availability")
    try:
        payload = response.json()
    except ValueError:
        raise CheckinError(path + ": response is not JSON") from None
    if not isinstance(payload, dict):
        raise CheckinError(path + ": expected a JSON object")
    code = payload.get("code")
    # Do not log arbitrary response bodies, account data, or credentials.
    label = str(code) if type(code) is int else "missing/non-integer"
    print(path + ": API code=" + label)
    message = str(payload.get("message", ""))
    already = ("please try tomorrow", "already checked in", "already checked-in", "already checkin")
    if path == "/api/user/checkin" and code == 1 and any(text in message.lower() for text in already):
        print("Already checked in today; no further action needed")
        payload["_already_checked_in"] = True
        return payload
    if code == 4 and payload.get("reason") == "device-mismatch":
        raise CheckinError(path + ": device-mismatch; USER_AGENT must match the browser used to obtain COOKIE")
    if type(code) is not int or code != 0:
        # Redact credentials and identifier-like strings from short business error messages.
        secrets = [os.getenv("COOKIE", ""), os.getenv("SCKEY", "")]
        secrets += [part.partition("=")[2].strip() for part in os.getenv("COOKIE", "").split(";")]
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            message = message.replace(secret, "[redacted]")
        message = re.sub(r"[A-Za-z0-9_@./+=:-]{20,}", "[redacted]", message)
        print("API message: " + repr(message[:200]))
        raise CheckinError(path + ": API rejected request (code=" + label + "); verify the complete current Cookie")
    return payload


def notify(content):
    if os.getenv("SERVE", "off").strip().lower() != "on":
        return
    key = os.getenv("SCKEY", "").strip()
    if not key:
        print("WARNING: notifications enabled but SCKEY is missing")
        return
    try:
        response = requests.post("https://sctapi.ftqq.com/" + key + ".send",
                                 data={"title": "GLaDOS check-in", "desp": content},
                                 timeout=30, allow_redirects=False)
        result = response.json()
        if response.status_code != 200 or not isinstance(result, dict) or result.get("code") != 0:
            print("WARNING: notification delivery failed; check SCKEY/provider")
        else:
            print("Notification delivered")
    except (requests.RequestException, ValueError):
        print("WARNING: notification request failed (credentials omitted)")


def start():
    try:
        cookie = os.getenv("COOKIE", "").strip()
        if not cookie:
            raise CheckinError("COOKIE Secret is missing or empty")
        if cookie.lower().startswith("cookie:") or "\r" in cookie or "\n" in cookie:
            raise CheckinError("COOKIE must be a single header value without the Cookie: prefix")
        with requests.Session() as session:
            session.headers.update({"Cookie": cookie, "Referer": BASE + "/console/checkin",
                                    "User-Agent": os.getenv("USER_AGENT", DEFAULT_USER_AGENT)})
            # Confirm authentication before attempting a check-in.
            state = api(session, "GET", "/api/user/status")
            data = state.get("data")
            if not isinstance(data, dict) or "leftDays" not in data:
                raise CheckinError("Account status is missing data.leftDays; API response schema changed")
            print("Authentication OK; account status contains data.leftDays")
            result = api(session, "POST", "/api/user/checkin", json={"token": "glados.cloud"})
            entries = result.get("list")
            first = entries[0] if isinstance(entries, list) and entries else {}
            balance = number_text(first.get("balance")) if isinstance(first, dict) else "unknown"
            days = number_text(data["leftDays"])
            outcome = "Already checked in today" if result.get("_already_checked_in") else "Check-in accepted"
            content = outcome + "; remaining days: " + days + "; points: " + balance
            print(content)
        notify(content)
        return 0
    except CheckinError as error:
        print("ERROR: " + str(error))
        notify("GLaDOS check-in failed: " + str(error))
        return 1


def main_handler(event, context):
    return start()


if __name__ == "__main__":
    sys.exit(start())
