"""Thin wrapper around ProPublica's Nonprofit Explorer API (no API key needed).

- search() returns organization profiles only (25 per page), no financials.
- get_org() returns an organization plus its filings, one call per EIN.
Responses are cached to disk so repeat runs don't re-hit the API.
"""
import hashlib
import json
import time
from pathlib import Path

import requests


class NonprofitClient:
    def __init__(self, api_config, session=None):
        self.base_url = api_config["base_url"].rstrip("/")
        self.timeout = api_config.get("timeout_seconds", 20)
        self.pause = api_config.get("pause_seconds", 0.5)
        self.max_retries = api_config.get("max_retries", 3)
        self.cache_dir = Path(api_config.get("cache_dir", ".cache"))
        self.session = session or requests.Session()

    # -- public methods -------------------------------------------------

    def search(self, q=None, state=None, ntee=None, c_code=None, page=0):
        params = {"page": page}
        if q:
            params["q"] = q
        if state:
            params["state[id]"] = state
        if ntee:
            params["ntee[id]"] = ntee
        if c_code:
            params["c_code[id]"] = c_code
        return self._get("/search.json", params)

    def get_org(self, ein):
        ein = str(ein).replace("-", "")
        return self._get(f"/organizations/{ein}.json", {})

    # -- internals ------------------------------------------------------

    def _cache_path(self, path, params):
        key = path + json.dumps(params, sort_keys=True)
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    def _get(self, path, params):
        cache_file = self._cache_path(path, params)
        if cache_file.exists():
            return json.loads(cache_file.read_text())

        url = self.base_url + path
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 404:
                    return None
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                data = resp.json()
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(data))
                time.sleep(self.pause)
                return data
            except (requests.RequestException, ValueError) as err:
                last_error = err
                time.sleep(self.pause * 2 ** attempt)  # back off before retrying
        raise RuntimeError(f"Request failed after {self.max_retries} tries: {url} ({last_error})")
