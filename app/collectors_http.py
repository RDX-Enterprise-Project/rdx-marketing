"""Real HTTP transport.

Imported lazily by the entry points so the unit tests never touch a network
stack, and so a publisher can always be swapped for a fake.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class RequestsHttpClient:
    def __init__(self, user_agent: str = "rdx-marketing/1.0") -> None:
        import requests

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})

    def post_json(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        response = self._session.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        response = self._session.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json()
