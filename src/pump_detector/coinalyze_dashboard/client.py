from __future__ import annotations

import time
from typing import Any, Callable

import requests


BASE_URL = "https://api.coinalyze.net/v1"


class CoinalyzeApiError(RuntimeError):
    pass


class CoinalyzeClient:
    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        timeout: int = 15,
    ) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()
        self.sleep_fn = sleep_fn or time.sleep
        self.timeout = timeout

    def get(self, endpoint: str, params: dict[str, object] | None = None) -> Any:
        """GET one Coinalyze endpoint and retry once on HTTP 429."""
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        url = f"{BASE_URL}{path}"
        headers = {"api_key": self.api_key, "Accept": "application/json"}
        request_params = params or {}

        for attempt in range(2):
            response = self.session.get(
                url,
                headers=headers,
                params=request_params,
                timeout=self.timeout,
            )
            if response.status_code == 429 and attempt == 0:
                retry_after = _to_retry_after(response.headers.get("Retry-After"))
                self.sleep_fn(retry_after)
                continue
            if response.status_code != 200:
                raise CoinalyzeApiError(f"Coinalyze {path} returned HTTP {response.status_code}")
            return response.json()

        raise CoinalyzeApiError(f"Coinalyze {path} remained rate limited")


def _to_retry_after(value: object) -> float:
    try:
        parsed = float(value)
        return parsed if parsed >= 0 else 60.0
    except (TypeError, ValueError):
        return 60.0
