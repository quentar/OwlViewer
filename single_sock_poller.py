"""Poll one Owlet sock efficiently.

Usage: python3 single_sock_poller.py 1

The number is a one-based device slot after sorting devices by serial number.
The script caches tokens and serials in readable local files, then fetches only
the selected sock's properties every 11 seconds.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import time
from typing import Any, TextIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp

from src.pyowletapi.api import OwletAPI
from src.pyowletapi.const import REGION_INFO
from src.pyowletapi.exceptions import OwletError


POLL_INTERVAL_SECONDS = 11
TOKEN_CACHE_PATH = Path(__file__).with_name("example-token.txt")
SERIAL_CACHE_PATH = Path(__file__).with_name("example-serials.txt")
DATA_LOG_PATH = Path(__file__).with_name("example-data.jsonl")


class OwletRequestError(OwletError):
    """An Owlet API response that cannot be handled by this small poller."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def load_token_cache(region: str, username: str) -> dict[str, Any]:
    try:
        cached = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        print(f"Ignoring unreadable token cache: {error}")
        return {}

    if cached.get("region") != region or cached.get("username") != username:
        print("Ignoring token cache because it belongs to a different account.")
        return {}

    token = cached.get("api_token")
    expiry = cached.get("expiry")
    refresh = cached.get("refresh")
    if not isinstance(token, str) or not isinstance(expiry, (int, float)):
        print("Ignoring incomplete token cache.")
        return {}
    if refresh is not None and not isinstance(refresh, str):
        print("Ignoring invalid token cache.")
        return {}

    return {"api_token": token, "expiry": float(expiry), "refresh": refresh}


def save_token_cache(region: str, username: str, tokens: dict[str, Any]) -> None:
    payload = {
        "region": region,
        "username": username,
        "api_token": tokens.get("api_token"),
        "expiry": tokens.get("expiry"),
        "refresh": tokens.get("refresh"),
    }
    TOKEN_CACHE_PATH.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(TOKEN_CACHE_PATH, 0o600)


def load_serials() -> list[str]:
    try:
        return [
            line.strip()
            for line in SERIAL_CACHE_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except FileNotFoundError:
        return []


def save_serials(serials: list[str]) -> None:
    SERIAL_CACHE_PATH.write_text("".join(f"{serial}\n" for serial in serials), encoding="utf-8")


def redact_url(url: Any) -> str:
    """Show the endpoint while hiding API keys and other query values."""
    parts = urlsplit(str(url))
    query = urlencode([(key, "<redacted>") for key, _value in parse_qsl(parts.query)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def response_body_for_log(response: aiohttp.ClientResponse, body: bytes) -> Any:
    text = body.decode(response.charset or "utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def write_data_log(data_log: TextIO, record: dict[str, Any]) -> None:
    data_log.write(json.dumps(record, default=str) + "\n")
    data_log.flush()


def create_traced_session(data_log: TextIO) -> aiohttp.ClientSession:
    """Create a session that numbers and records every HTTP response."""
    trace_config = aiohttp.TraceConfig()
    request_count = 0

    async def request_started(
        _session: aiohttp.ClientSession,
        context: Any,
        params: Any,
    ) -> None:
        nonlocal request_count
        request_count += 1
        context.request_number = request_count
        print(
            f"[request {request_count:03d}] START {params.method} {redact_url(params.url)}",
            flush=True,
        )

    async def request_finished(
        _session: aiohttp.ClientSession,
        context: Any,
        params: Any,
    ) -> None:
        body = await params.response.read()
        print(
            f"[request {context.request_number:03d}] DONE  "
            f"HTTP {params.response.status} {params.response.reason}",
            flush=True,
        )
        write_data_log(
            data_log,
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "request_number": context.request_number,
                "method": params.method,
                "url": str(params.url),
                "status": params.response.status,
                "reason": params.response.reason,
                "headers": dict(params.response.headers),
                "body": response_body_for_log(params.response, body),
            },
        )

    async def request_failed(
        _session: aiohttp.ClientSession,
        context: Any,
        params: Any,
    ) -> None:
        print(
            f"[request {context.request_number:03d}] ERROR {params.exception!r}",
            flush=True,
        )
        write_data_log(
            data_log,
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "request_number": context.request_number,
                "method": params.method,
                "url": str(params.url),
                "exception": repr(params.exception),
            },
        )

    trace_config.on_request_start.append(request_started)
    trace_config.on_request_end.append(request_finished)
    trace_config.on_request_exception.append(request_failed)
    return aiohttp.ClientSession(trace_configs=[trace_config])


async def request_json(
    api: OwletAPI,
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
) -> Any:
    """Make one API request without an extra /devices.json validation request."""
    await api.authenticate()
    url = REGION_INFO[api._region]["url_base"] + path  # API base follows the chosen region.
    async with api.session.request(method, url, headers=api.headers, json=data) as response:
        if response.status not in (200, 201):
            body = await response.text()
            raise OwletRequestError(
                f"{method} {path} returned HTTP {response.status} {response.reason}: {body[:500]}",
                status=response.status,
            )
        return await response.json()


async def get_selected_properties(api: OwletAPI, serial: str) -> dict[str, dict[str, Any]]:
    raw_properties = await request_json(
        api,
        "GET",
        f"/dsns/{serial}/properties.json",
    )
    return {
        item["property"]["name"]: item["property"]
        for item in raw_properties
    }


def print_properties(serial: str, properties: dict[str, dict[str, Any]]) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"\n{timestamp} — {serial}")
    print(json.dumps(properties, separators=(",", ":"), default=str))


async def run(device_number: int) -> None:
    with open("login.json", encoding="utf-8") as login_file:
        login = json.load(login_file)

    region = login["region"]
    username = login["username"]
    cached_tokens = load_token_cache(region, username)
    if cached_tokens:
        if cached_tokens["expiry"] > time.time():
            print(f"Using valid token cache: {TOKEN_CACHE_PATH}")
        else:
            print(f"Cached access token expired; using its refresh token: {TOKEN_CACHE_PATH}")

    data_log = DATA_LOG_PATH.open("a", encoding="utf-8", buffering=1)
    os.chmod(DATA_LOG_PATH, 0o600)
    print(f"Appending every HTTP response to {DATA_LOG_PATH}")

    session = create_traced_session(data_log)
    api = OwletAPI(
        region,
        username,
        login["password"],
        token=cached_tokens.get("api_token"),
        expiry=cached_tokens.get("expiry"),
        refresh=cached_tokens.get("refresh"),
        session=session,
    )
    try:
        await api.authenticate()
        save_token_cache(region, username, api.tokens)
        last_saved_tokens = dict(api.tokens)

        serials = load_serials()
        if serials:
            print(f"Using cached serials from {SERIAL_CACHE_PATH}")
        else:
            devices = await request_json(api, "GET", "/devices.json")
            if not isinstance(devices, list):
                raise OwletRequestError("Unexpected response while discovering devices")
            serials = sorted({item["device"]["dsn"] for item in devices})
            save_serials(serials)
            print(f"Saved sorted serials to {SERIAL_CACHE_PATH}")

        if not 1 <= device_number <= len(serials):
            available = ", ".join(
                f"{index}: {serial}"
                for index, serial in enumerate(serials, start=1)
            )
            raise OwletRequestError(
                f"Device number must be between 1 and {len(serials)}. Available: {available}",
            )

        serial = serials[device_number - 1]
        print(f"Polling device {device_number}: {serial}")
        print(f"One properties request every {POLL_INTERVAL_SECONDS} seconds. Press Ctrl-C to stop.")

        while True:
            try:
                properties = await get_selected_properties(api, serial)
                print_properties(serial, properties)
            except OwletRequestError as error:
                if error.status not in {429, 502, 503, 504}:
                    raise
                print(
                    f"Transient Owlet API failure: {error}. "
                    f"Retrying in {POLL_INTERVAL_SECONDS} seconds.",
                    flush=True,
                )
            if api.tokens != last_saved_tokens:
                save_token_cache(region, username, api.tokens)
                last_saved_tokens = dict(api.tokens)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        await api.close()
        data_log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll exactly one Owlet sock every 11 seconds.")
    parser.add_argument("device", type=int, help="one-based sock number after sorting by serial")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.device))
    except KeyboardInterrupt:
        print("\nStopped.")
    except (OwletError, aiohttp.ClientError) as error:
        print(f"Owlet API error: {error}")
