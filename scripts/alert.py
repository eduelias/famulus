#!/usr/bin/env python3
"""Send a WhatsApp alert through the Meta Cloud API with an approved template.

    python3 scripts/alert.py SOURCE DETAIL [--dry-run]

For GitHub Actions (failed releases and deploys), which must reach the owner when du7 or the
home internet is down, and outside WhatsApp's 24-hour reply window: plain text messages are
only delivered inside it, templates always are. The template has two variables, {{1}} the
source and {{2}} the detail. Same script and template as the Pauta repo.

Environment: WA_TOKEN, WA_PHONE_ID, WA_TO (the owner's number, digits only), WA_TEMPLATE
(default du7_service_status) and WA_TEMPLATE_LANG (default en). Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

GRAPH = "https://graph.facebook.com/v21.0"
MAX_PARAM = 700  # the whole template body may be at most 1024 characters


def param(text: str, limit: int = MAX_PARAM) -> str:
    """Template variables may not hold newlines, tabs or more than 4 spaces in a row."""
    flat = re.sub(r"\s*\n\s*", " · ", text.strip())
    flat = re.sub(r"[\t ]+", " ", flat)
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def payload(to: str, source: str, detail: str, template: str, lang: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": param(source, 60)},
                        {"type": "text", "text": param(detail)},
                    ],
                }
            ],
        },
    }


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    env = os.environ
    body = payload(
        env.get("WA_TO", "") or "<WA_TO>",
        args[0],
        args[1],
        env.get("WA_TEMPLATE") or "du7_service_status",
        env.get("WA_TEMPLATE_LANG") or "en",
    )
    if dry:
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0
    missing = [k for k in ("WA_TOKEN", "WA_PHONE_ID", "WA_TO") if not env.get(k)]
    if missing:
        print(f"alert not sent: {', '.join(missing)} not set", file=sys.stderr)
        return 1
    req = urllib.request.Request(
        f"{GRAPH}/{env['WA_PHONE_ID']}/messages",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {env['WA_TOKEN']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            sent = json.load(r).get("messages", [{}])[0].get("id", "?")
    except urllib.error.HTTPError as e:
        print(f"alert not sent: HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        return 1
    print(f"WhatsApp alert sent ({sent})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
