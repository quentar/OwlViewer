from src.pyowletapi.api import OwletAPI
from src.pyowletapi.sock import Sock
from src.pyowletapi.exceptions import OwletError

import aiohttp
import asyncio
import json
import ssl


def create_client_session():
    try:
        import certifi
    except ImportError:
        print("Tip: install certifi with `python3 -m pip install certifi` if SSL fails.")
        ssl_context = ssl.create_default_context()
    else:
        ssl_context = ssl.create_default_context(cafile=certifi.where())

    connector = aiohttp.TCPConnector(ssl=ssl_context)
    return aiohttp.ClientSession(connector=connector)


async def run():
    with open("login.json") as file:
        data = json.load(file)
    region = data["region"]
    username = data["username"]
    password = data["password"]

    session = create_client_session()
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
        print(err)
    except aiohttp.ClientConnectorCertificateError as err:
        print(err)
        print("SSL certificate verification failed.")
        print("Try: python3 -m pip install certifi")
        print("On python.org macOS builds, also try running the bundled Install Certificates.command.")
    finally:
        await api.close()


if __name__ == "__main__":
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
