# OwlViewer

Simple desktop telemetry viewer that polls connected socks every 10 seconds.

## Prerequisites

- Python 3.11+
- `login.json` in the project root (`pyowletapi/login.json`) with:

```json
{
  "region": "world",
  "username": "you@example.com",
  "password": "your_password"
}
```

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
