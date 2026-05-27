import asyncio
import json
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import wx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
LAYOUT_PATH = PROJECT_ROOT / "wx_monitor_app" / "layout.json"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pyowletapi.api import OwletAPI
from pyowletapi.exceptions import OwletError
from pyowletapi.sock import Sock


class MetricTile(wx.Panel):
    def __init__(self, parent: wx.Window, label: str, width: int, height: int) -> None:
        super().__init__(parent)
        self.SetMinSize((width, height))
        self.SetBackgroundColour(wx.Colour(245, 246, 248))

        border = wx.BoxSizer(wx.VERTICAL)
        inner = wx.Panel(self)
        inner.SetBackgroundColour(wx.Colour(255, 255, 255))

        title = wx.StaticText(inner, label=label)
        title.SetFont(wx.Font(12, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        title.SetForegroundColour(wx.Colour(35, 35, 35))

        self.value = wx.StaticText(inner, label="--")
        self.value.SetFont(wx.Font(24, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self.value.SetForegroundColour(wx.Colour(20, 20, 20))

        inner_layout = wx.BoxSizer(wx.VERTICAL)
        inner_layout.Add(title, flag=wx.ALL, border=10)
        inner_layout.Add(self.value, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        inner.SetSizer(inner_layout)

        border.Add(inner, proportion=1, flag=wx.ALL | wx.EXPAND, border=3)
        self.SetSizer(border)

    def set_value(self, value: str) -> None:
        self.value.SetLabel(value)


class OwletMonitorFrame(wx.Frame):
    def __init__(self) -> None:
        self.layout_config = self._load_layout_config()
        title = str(self.layout_config.get("title", "Owlet Monitor"))
        super().__init__(parent=None, title=title, size=(1040, 760))

        panel = wx.Panel(self)
        self.status = wx.StaticText(panel, label="Idle")
        self.start_btn = wx.Button(panel, label="Start")
        self.stop_btn = wx.Button(panel, label="Stop")
        self.stop_btn.Disable()

        self.tiles: dict[str, MetricTile] = {}
        self.ordered_properties: list[str] = []

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        button_row.Add(self.start_btn, flag=wx.RIGHT, border=8)
        button_row.Add(self.stop_btn)

        self.tiles_wrap = wx.WrapSizer(wx.HORIZONTAL)
        for box in self.layout_config["boxes"]:
            prop = str(box["property"])
            label = str(box.get("label", prop))
            width = int(box.get("width", 260))
            height = int(box.get("height", 130))
            tile = MetricTile(panel, label=label, width=width, height=height)
            self.tiles[prop] = tile
            self.ordered_properties.append(prop)
            self.tiles_wrap.Add(tile, flag=wx.ALL, border=6)

        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(self.status, flag=wx.ALL | wx.EXPAND, border=10)
        layout.Add(button_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        layout.Add(self.tiles_wrap, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        panel.SetSizer(layout)

        self.poll_seconds = int(self.layout_config.get("poll_seconds", 10))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.start_btn.Bind(wx.EVT_BUTTON, self.on_start)
        self.stop_btn.Bind(wx.EVT_BUTTON, self.on_stop)
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def _load_layout_config(self) -> dict[str, Any]:
        with LAYOUT_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if "boxes" not in data or not isinstance(data["boxes"], list):
            raise ValueError(f"Invalid layout config: missing boxes list in {LAYOUT_PATH}")

        return data

    def set_status(self, text: str) -> None:
        self.status.SetLabel(text)

    def set_error(self, message: str) -> None:
        self.set_status(f"Error: {message}")

    def on_start(self, _event: wx.CommandEvent) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self.start_btn.Disable()
        self.stop_btn.Enable()
        self.set_status("Starting...")

        self._thread = threading.Thread(target=self._run_monitor, daemon=True)
        self._thread.start()

    def on_stop(self, _event: wx.CommandEvent) -> None:
        self._request_stop()

    def on_close(self, event: wx.CloseEvent) -> None:
        self._request_stop()
        event.Skip()

    def _request_stop(self) -> None:
        self._stop_event.set()
        self.start_btn.Enable()
        self.stop_btn.Disable()
        self.set_status("Stopped")

    def _run_monitor(self) -> None:
        try:
            asyncio.run(self._monitor_loop())
        except Exception:
            wx.CallAfter(self.set_error, traceback.format_exc().splitlines()[-1])
            wx.CallAfter(self.start_btn.Enable)
            wx.CallAfter(self.stop_btn.Disable)

    async def _monitor_loop(self) -> None:
        api: OwletAPI | None = None
        try:
            config = self._load_login_config()
            wx.CallAfter(self.set_status, "Authenticating...")

            api = OwletAPI(config["region"], config["username"], config["password"])
            await api.authenticate()

            devices = await api.get_devices()
            socks = {
                device["device"]["dsn"]: Sock(api, device["device"])
                for device in devices["response"]
            }

            if not socks:
                raise OwletError("No devices found")

            wx.CallAfter(
                self.set_status,
                f"Running ({len(socks)} device(s), polling every {self.poll_seconds}s)",
            )

            while not self._stop_event.is_set():
                props = await self._poll_once(socks)
                wx.CallAfter(self._apply_metrics, props)
                for _ in range(self.poll_seconds):
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1)

        except (OwletError, KeyError, FileNotFoundError, json.JSONDecodeError, ValueError) as err:
            wx.CallAfter(self.set_error, str(err))
        finally:
            if api is not None:
                await api.close()
            wx.CallAfter(self.start_btn.Enable)
            wx.CallAfter(self.stop_btn.Disable)

    def _load_login_config(self) -> dict[str, str]:
        login_path = PROJECT_ROOT / "login.json"
        with login_path.open("r", encoding="utf-8") as file:
            data: dict[str, str] = json.load(file)

        for required_key in ("region", "username", "password"):
            if required_key not in data:
                raise KeyError(f"Missing '{required_key}' in {login_path}")

        return data

    async def _poll_once(self, socks: dict[str, Sock]) -> dict[str, Any]:
        first_sock = next(iter(socks.values()))
        result = await first_sock.update_properties()
        return result["properties"]

    def _apply_metrics(self, props: dict[str, Any]) -> None:
        for prop in self.ordered_properties:
            tile = self.tiles[prop]
            raw_value = props.get(prop)
            tile.set_value(self._format_metric(prop, raw_value))

    def _format_metric(self, key: str, value: Any) -> str:
        if value is None:
            return "--"

        if key == "monitoring_start_time":
            try:
                epoch = int(value)
                return datetime.fromtimestamp(epoch).strftime("%Y/%m/%d %H:%M:%S")
            except (ValueError, TypeError, OSError):
                return str(value)

        return str(value)


class OwletMonitorApp(wx.App):
    def OnInit(self) -> bool:
        frame = OwletMonitorFrame()
        frame.Show()
        return True


if __name__ == "__main__":
    app = OwletMonitorApp(False)
    app.MainLoop()
