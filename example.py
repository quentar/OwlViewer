from src.pyowletapi.api import OwletAPI
from src.pyowletapi.sock import Sock
from src.pyowletapi.exceptions import OwletError

import argparse
import aiohttp
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import ssl
import sys
from typing import Any, TextIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_FIELDS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "id_token",
    "mini_token",
    "password",
    "refresh_token",
    "refreshtoken",
    "token",
}


def redact(value: Any, field_name: str | None = None) -> Any:
    """Remove credentials from diagnostic output before it reaches disk."""
    if field_name and field_name.lower() in SENSITIVE_FIELDS:
        return "<redacted>"
    if isinstance(value, dict):
        return {key: redact(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def redact_url(url: Any) -> str:
    """Keep the endpoint but omit values from URL query parameters."""
    parts = urlsplit(str(url))
    query = urlencode([(key, "<redacted>") for key, _value in parse_qsl(parts.query)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def response_body_for_log(response: aiohttp.ClientResponse, body: bytes) -> Any:
    text = body.decode(response.charset or "utf-8", errors="replace")
    try:
        return redact(json.loads(text))
    except json.JSONDecodeError:
        return text


def write_debug_record(debug_file: TextIO, record: dict[str, Any]) -> None:
    debug_file.write(json.dumps(record, default=str) + "\n")
    debug_file.flush()


def create_client_session(debug_file: TextIO | None = None):
    try:
        import certifi
    except ImportError:
        print("Tip: install certifi with `python3 -m pip install certifi` if SSL fails.")
        ssl_context = ssl.create_default_context()
    else:
        ssl_context = ssl.create_default_context(cafile=certifi.where())

    trace_config = aiohttp.TraceConfig()

    async def print_response_status(
        _session: aiohttp.ClientSession,
        _context: Any,
        params: Any,
    ) -> None:
        print(
            f"HTTP {params.method} {redact_url(params.url)}: "
            f"{params.response.status} {params.response.reason}",
            file=sys.stderr,
        )
        if debug_file is not None:
            body = await params.response.read()
            write_debug_record(
                debug_file,
                {
                    "event": "response",
                    "method": params.method,
                    "url": redact_url(params.url),
                    "status": params.response.status,
                    "reason": params.response.reason,
                    "headers": redact(dict(params.response.headers)),
                    "body": response_body_for_log(params.response, body),
                },
            )

    async def print_request_exception(
        _session: aiohttp.ClientSession,
        _context: Any,
        params: Any,
    ) -> None:
        print(
            f"HTTP {params.method} {redact_url(params.url)}: {params.exception!r}",
            file=sys.stderr,
        )
        if debug_file is not None:
            write_debug_record(
                debug_file,
                {
                    "event": "exception",
                    "method": params.method,
                    "url": redact_url(params.url),
                    "exception": repr(params.exception),
                },
            )

    trace_config.on_request_end.append(print_response_status)
    trace_config.on_request_exception.append(print_request_exception)

    connector = aiohttp.TCPConnector(ssl=ssl_context)
    return aiohttp.ClientSession(connector=connector, trace_configs=[trace_config])


async def run(debug: bool = False):
    with open("login.json") as file:
        data = json.load(file)
    region = data["region"]
    username = data["username"]
    password = data["password"]

    debug_file: TextIO | None = None
    if debug:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        debug_path = Path(f"owlet-api-debug-{timestamp}.jsonl")
        debug_file = debug_path.open("x", encoding="utf-8")
        print(f"Writing redacted API debug log to {debug_path.resolve()}", file=sys.stderr)

    session = create_client_session(debug_file)
    api = OwletAPI(region, username, password, session=session)

    try:
        await api.authenticate()

        devices = await api.get_devices()
        print("Raw devices response:")
        print(json.dumps(devices, indent=2, default=str))

        socks = [Sock(api, device["device"]) for device in devices["response"]]

        print("Devices:")
        for index, sock in enumerate(socks, start=1):
            print(f"  Sock {index}: {sock.name} ({sock.serial})")

        for sock in socks:
            properties = (await sock.update_properties())["properties"]
            print(f"{sock.name} ({sock.serial}):")
            print(properties)

    except OwletError as err:
        print(f"Owlet API error: {err!r}", file=sys.stderr)
    except aiohttp.ClientConnectorCertificateError as err:
        print(err)
        print("SSL certificate verification failed.")
        print("Try: python3 -m pip install certifi")
        print("On python.org macOS builds, also try running the bundled Install Certificates.command.")
    finally:
        await api.close()
        if debug_file is not None:
            debug_file.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read current Owlet monitor data.")
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="write a redacted HTTP request/response trace to a JSONL file",
    )
    args = parser.parse_args()
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run(debug=args.debug))
