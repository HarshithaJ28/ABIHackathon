"""API client with retry logic for the PCC mock API.

Every request has a 30% chance of HTTP 429.
Retry-After header is a random int between 1-5 seconds.
"""

from typing import Union

import time

import requests

from config import BASE_URL, MAX_RETRIES


def api_get(endpoint: str, params: dict = None) -> Union[list, dict]:
    """GET request with automatic retry on 429.

    Returns parsed JSON response.
    """
    url = f"{BASE_URL}{endpoint}"

    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.get(url, params=params)

        if response.status_code == 200:
            return response.json()

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 2))
            # Add exponential backoff on top of Retry-After
            wait_time = retry_after + (attempt - 1)
            print(
                f"429 on {endpoint} (attempt {attempt}/{MAX_RETRIES}) "
                f"— waiting {wait_time}s"
            )
            time.sleep(wait_time)
            continue

        # 422 = bad params, 500 = server error — raise immediately
        response.raise_for_status()

    raise Exception(
        f"Failed after {MAX_RETRIES} retries: GET {endpoint} params={params}"
    )

