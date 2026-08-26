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

Click **Start** to authenticate and begin polling every 10s, and **Stop** to pause.
