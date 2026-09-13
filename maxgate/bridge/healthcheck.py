"""Read-only healthcheck без вывода Bearer и тела ответа."""

import os
from urllib.request import Request, urlopen


def main():
    token = os.environ.get("MAXGATE_INTERNAL_TOKEN", "")
    if not token:
        raise SystemExit(1)
    request = Request(
        "http://127.0.0.1:" + os.environ.get("MAXGATE_API_PORT", "8080") + "/health",
        headers={"Authorization": "Bearer " + token},
    )
    try:
        with urlopen(request, timeout=3) as response:
            raise SystemExit(0 if response.status == 200 else 1)
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
