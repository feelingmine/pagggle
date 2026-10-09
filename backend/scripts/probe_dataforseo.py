"""Run one paid request for the user-authorized fixed project and keyword.

Usage: PYTHONPATH=backend .venv/bin/python backend/scripts/probe_dataforseo.py
No automatic retries: each invocation can incur another API charge.
"""

import argparse
import json
from datetime import datetime, timezone
from time import monotonic

import httpx

from pagggle.config import ROOT, load_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-response", action="store_true", help="Use /live instead of /live.ai")
    args = parser.parse_args()
    settings = load_settings()
    login = settings.dataforseo_login.get_secret_value()
    password = settings.dataforseo_password.get_secret_value()
    if not login or not password:
        raise SystemExit("Set dataforseo_login and dataforseo_password in config.json.")

    project_id = "3569ed46f2554178b327c2e7c58c8b3b"
    url = "https://api.dataforseo.com/v3/keywords_data/google_ads/keywords_for_keywords/live.ai"
    if args.full_response:
        url = url.removesuffix(".ai")
    payload = [{"keywords": ["silicone baby feeding set"], "location_code": 2840,
                "language_code": "en", "sort_by": "search_volume"}]
    now = datetime.now(timezone.utc)
    output = ROOT / "data/verification" / project_id / "dataforseo" / now.strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    try:
        response = httpx.post(url, auth=(login, password), json=payload, timeout=120,
                              follow_redirects=False)
    except httpx.RequestError as error:
        # Do not expose headers or credentials through exception diagnostics.
        raise SystemExit(f"Request failed ({type(error).__name__}); no automatic retry.") from None

    # Preserve the original body; only print structural metadata, never headers.
    (output / "response.txt").write_bytes(response.content)
    summary = {"project_id": project_id, "requested_at_utc": now.isoformat(),
               "url": url, "payload": payload, "http_status": response.status_code,
               "elapsed_seconds": round(monotonic() - started, 3),
               "content_type": response.headers.get("content-type"),
               "response_bytes": len(response.content)}
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        summary.update({key: body.get(key) for key in
                        ("status_code", "status_message", "cost", "tasks_count", "tasks_error")})
        summary["tasks"] = [{key: task.get(key) for key in
                             ("status_code", "status_message", "cost", "result_count")}
                            for task in body.get("tasks", [])]
        if "items" in body:
            summary["items_count"] = len(body["items"] or [])
    (output / "request-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"Saved response: {output.relative_to(ROOT) / 'response.txt'}")
    if (response.status_code != 200 or not isinstance(body, dict)
            or body.get("status_code") != 20000
            or any(task.get("status_code") != 20000 for task in body.get("tasks", []))):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
