"""One HTTP GET with retries, shared by every ingest module.

Why this exists: the ingest modules caught `requests.HTTPError`, which is
raised only by raise_for_status(). Timeout and ConnectionError are NOT
subclasses of it, so a dropped connection on the first sport propagated out
of the loop - the remaining sports were never pulled, export_snapshots never
ran, and the whole slot was lost. There are only three pulls a day.

Retries are deliberately narrow:

  retry      timeouts, connection resets, and 5xx - transient, the same
             request will probably work in a moment
  do NOT     4xx. A 401 is a bad key and a 429 is out of credits; retrying
             either just burns time, and on a metered API it can burn the
             budget it is telling you that you already exhausted.

The Odds API charges per request, so a retry can cost a credit. That is the
right trade at 3 pulls/day against a 500/month cap - losing a whole pull
costs a day of closing lines - but it is a trade, which is why the retry
count is small and visible rather than a library default.
"""
import time

import requests

RETRY_ON = (requests.Timeout, requests.ConnectionError)
ATTEMPTS = 3
BACKOFF = 2.0          # seconds, doubled each attempt: 2s then 4s


def get(url, *, params=None, timeout=30, attempts=ATTEMPTS, label=""):
    """GET with retries on transient failures. Raises on permanent ones."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code >= 500:
                last = requests.HTTPError(
                    f"{r.status_code} {r.reason}", response=r)
                raise last
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            # A 4xx is the server telling you the request itself is wrong.
            # Retrying cannot change that.
            if e.response is not None and e.response.status_code < 500:
                raise
            last = e
        except RETRY_ON as e:
            last = e
        if attempt < attempts:
            wait = BACKOFF * (2 ** (attempt - 1))
            tag = f"[{label}] " if label else ""
            print(f"  {tag}attempt {attempt}/{attempts} failed ({type(last).__name__});"
                  f" retrying in {wait:.0f}s")
            time.sleep(wait)
    raise last
