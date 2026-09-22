# OwlViewer

Simple desktop telemetry viewer that polls connected socks every 10 seconds.

## Prerequisites

- Python 3.11+
- A `login.json` file in the project root (`pyowletapi/login.json`):

```json
{
  "region": "world",
  "username": "your-email@example.com",
  "password": "your-password"
}
```

Use `"europe"` instead of `"world"` for European accounts.

`login.json` is ignored by Git. Do not commit or share it.

## Install

```bash
pip install aiohttp certifi wxPython
```

## Run

From the `pyowletapi` folder:

```bash
python wx_monitor_app/app.py
```

Click **Start** to authenticate and begin polling, and **Stop** to pause.

The monitor stores authentication and device discovery in `owlet-token-cache.json` and
`owlet-device-cache.json`. A valid cached token and device list let later starts go directly
to telemetry requests. Delete either cache file to force authentication or device discovery
again.

Property polling and `APP_ACTIVE` use separate settings. Properties default to every 11
seconds, while `APP_ACTIVE` defaults to every 30 seconds. The monitor header shows the most
recent activation attempt as `Calling APP_ACTIVE HH:MM:SS`.

Legacy behavior is retained behind `LEGACY_REQUEST_BEHAVIOR` in `app.py`. Setting it to
`True` restores `/devices.json` validation before every API request and activation before
every property poll. It defaults to `False`.
