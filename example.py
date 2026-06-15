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
        socks = {
            device["device"]["dsn"]: Sock(api, device["device"])
            for device in devices["response"]
        }
        for sock in socks.values():
            # print(await sock._api.get_properties(sock.serial))
            properties = await sock.update_properties()
            properties = properties["properties"]
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
