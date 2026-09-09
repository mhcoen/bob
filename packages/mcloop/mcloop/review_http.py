"""Run one review HTTP exchange in a process with a parent-enforced deadline."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request


def exchange(request: urllib.request.Request, limit: int, *, timeout: float = 90) -> bytes:
    payload = {
        "url": request.full_url,
        "headers": dict(request.header_items()),
        "data": base64.b64encode(request.data or b"").decode(),
        "limit": limit,
        "timeout": timeout,
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mcloop.review_http"],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Review HTTP deadline expired") from exc
    if result.returncode:
        raise ConnectionError("Review HTTP worker failed")
    reply = json.loads(result.stdout)
    if "http_status" in reply:
        raise urllib.error.HTTPError(
            "",
            reply["http_status"],
            "Review HTTP failure",
            {"Retry-After": reply["retry_after"]} if reply.get("retry_after") else {},
            None,
        )
    if reply.get("error") == "timeout":
        raise TimeoutError("Review HTTP deadline expired")
    if "error" in reply:
        raise ConnectionError("Review HTTP connection failed")
    return base64.b64decode(reply["body"], validate=True)


def main() -> None:
    payload = json.load(sys.stdin)
    request = urllib.request.Request(
        payload["url"],
        data=base64.b64decode(payload["data"]),
        headers=payload["headers"],
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=payload["timeout"]) as response:
            body = response.read(payload["limit"])
        reply = {"body": base64.b64encode(body).decode()}
    except urllib.error.HTTPError as exc:
        reply = {"http_status": exc.code, "retry_after": exc.headers.get("Retry-After")}
    except TimeoutError:
        reply = {"error": "timeout"}
    except (urllib.error.URLError, OSError):
        reply = {"error": "connection"}
    print(json.dumps(reply))


if __name__ == "__main__":
    main()
