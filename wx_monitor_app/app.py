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


LEVEL_COLORS = {
    "normal": wx.Colour(255, 255, 255),
    "yellow": wx.Colour(255, 247, 204),
    "red": wx.Colour(255, 213, 213),
}
FLASH_RED = wx.Colour(255, 120, 120)
TEXT_COLOR = wx.Colour(20, 20, 20)
TITLE_COLOR = wx.Colour(35, 35, 35)


class MetricTile(wx.Panel):
    def __init__(self, parent: wx.Window, label: str, width: int, height: int) -> None:
        super().__init__(parent)
        self.SetMinSize((width, height))
        self.SetBackgroundColour(wx.Colour(245, 246, 248))

        self.inner = wx.Panel(self)
        self.inner.SetBackgroundColour(LEVEL_COLORS["normal"])

        self.title = wx.StaticText(self.inner, label=label)
        self.title.SetFont(wx.Font(12, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self.title.SetForegroundColour(TITLE_COLOR)

        self.value = wx.StaticText(self.inner, label="--")
        self.value.SetForegroundColour(TEXT_COLOR)
        self._set_best_font("--")

        inner_layout = wx.BoxSizer(wx.VERTICAL)
        inner_layout.Add(self.title, flag=wx.ALL, border=10)
        inner_layout.Add(self.value, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        self.inner.SetSizer(inner_layout)

        border = wx.BoxSizer(wx.VERTICAL)
        border.Add(self.inner, proportion=1, flag=wx.ALL | wx.EXPAND, border=3)
        self.SetSizer(border)

    def set_value(self, value: str) -> None:
        self._set_best_font(value)
        self.value.SetLabel(value)
        self.Layout()

    def set_alert_background(self, level: str, flash_on: bool = False) -> None:
        if level == "red" and flash_on:
            color = FLASH_RED
        else:
            color = LEVEL_COLORS.get(level, LEVEL_COLORS["normal"])
        self.inner.SetBackgroundColour(color)
        self.inner.Refresh()

    def _set_best_font(self, text: str) -> None:
        width = max(self.GetMinSize().GetWidth() - 24, 120)
        height = max(self.GetMinSize().GetHeight() - 54, 50)
        for point in range(62, 15, -1):
            font = wx.Font(point, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            self.value.SetFont(font)
            tw, th = self.value.GetTextExtent(text)
            if tw <= width and th <= height:
                return
        self.value.SetFont(wx.Font(16, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))


class OwletMonitorFrame(wx.Frame):
    def __init__(self) -> None:
        self.layout_config = self._load_layout_config()
        title = str(self.layout_config.get("title", "Owlet Monitor"))
        super().__init__(parent=None, title=title, size=(1120, 820))

        panel = wx.Panel(self)
        self.status = wx.StaticText(panel, label="Idle")
        self.start_btn = wx.Button(panel, label="Start")
        self.stop_btn = wx.Button(panel, label="Stop")
        self.stop_btn.Disable()

        self.tiles: dict[str, MetricTile] = {}
        self.box_config: dict[str, dict[str, Any]] = {}
        self.ordered_properties: list[str] = []
        self._consecutive_counts: dict[str, int] = {}
        self._current_levels: dict[str, str] = {}
        self._flashing_keys: set[str] = set()
        self._flash_on = False

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
            self.box_config[prop] = box
            self.ordered_properties.append(prop)
            self._consecutive_counts[prop] = 0
            self._current_levels[prop] = "normal"
            self.tiles_wrap.Add(tile, flag=wx.ALL, border=6)

        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(self.status, flag=wx.ALL | wx.EXPAND, border=10)
        layout.Add(button_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        layout.Add(self.tiles_wrap, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        panel.SetSizer(layout)

        self.poll_seconds = int(self.layout_config.get("poll_seconds", 10))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.flash_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_flash_timer, self.flash_timer)

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
        self._flashing_keys.clear()
        self.flash_timer.Stop()
        self._apply_alert_backgrounds()
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
            socks = {device["device"]["dsn"]: Sock(api, device["device"]) for device in devices["response"]}
            if not socks:
                raise OwletError("No devices found")

            wx.CallAfter(self.set_status, f"Running ({len(socks)} device(s), polling every {self.poll_seconds}s)")
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
        now = datetime.now()
        for prop in self.ordered_properties:
            tile = self.tiles[prop]
            config = self.box_config[prop]
            raw_value = props.get(prop)
            tile.set_value(self._format_metric(config, raw_value, now))
            self._current_levels[prop] = self._evaluate_alert_level(config, raw_value)

        self._flashing_keys = {
            prop
            for prop in self.ordered_properties
            if self._current_levels.get(prop) == "red" and self._should_flash_red(self.box_config[prop])
        }

        if self._flashing_keys and not self.flash_timer.IsRunning():
            self.flash_timer.Start(500)
        if not self._flashing_keys and self.flash_timer.IsRunning():
            self.flash_timer.Stop()
            self._flash_on = False

        self._apply_alert_backgrounds()

    def _apply_alert_backgrounds(self) -> None:
        for prop in self.ordered_properties:
            level = self._current_levels.get(prop, "normal")
            flash_on = self._flash_on and prop in self._flashing_keys
            self.tiles[prop].set_alert_background(level, flash_on)

    def _on_flash_timer(self, _event: wx.TimerEvent) -> None:
        self._flash_on = not self._flash_on
        self._apply_alert_backgrounds()

    def _evaluate_alert_level(self, config: dict[str, Any], value: Any) -> str:
        alerts = config.get("alerts")
        if not alerts:
            return "normal"

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            self._consecutive_counts[config["property"]] = 0
            return "normal"

        prop = str(config["property"])
        rules: list[dict[str, Any]] = [r for r in alerts if r.get("when") == "below"]
        if not rules:
            return "normal"

        threshold = float(rules[0].get("threshold", -1))
        if numeric_value < threshold:
            self._consecutive_counts[prop] = self._consecutive_counts.get(prop, 0) + 1
        else:
            self._consecutive_counts[prop] = 0

        count = self._consecutive_counts[prop]
        level = "normal"
        for rule in sorted(rules, key=lambda r: int(r.get("consecutive", 0))):
            needed = int(rule.get("consecutive", 0))
            if count >= needed:
                level = str(rule.get("name", "normal"))
        return level

    def _should_flash_red(self, config: dict[str, Any]) -> bool:
        alerts = config.get("alerts") or []
        for rule in alerts:
            if str(rule.get("name")) == "red" and bool(rule.get("flash", False)):
                return True
        return False

    def _format_metric(self, config: dict[str, Any], value: Any, now: datetime) -> str:
        if value is None:
            return "--"

        value_type = str(config.get("type", "plain"))

        if value_type == "bpm":
            return f"{int(float(value))} bpm"
        if value_type == "percent":
            return f"{int(float(value))}%"
        if value_type == "minutes_duration":
            total = int(float(value))
            hours = total // 60
            mins = total % 60
            return f"{hours}h {mins}m"
        if value_type == "epoch_with_age":
            try:
                dt = datetime.fromtimestamp(int(value))
                age = self._human_age(now - dt)
                return f"{dt.strftime('%H:%M:%S')}\n{age}"
            except (ValueError, TypeError, OSError):
                return str(value)
        if value_type == "refreshed_age_seconds":
            try:
                dt = datetime.strptime(str(value), "%Y/%m/%d %H:%M:%S")
                age_s = max(int((now - dt).total_seconds()), 0)
                return f"{age_s}s ago"
            except ValueError:
                return str(value)
        if value_type == "int":
            return str(int(float(value)))

        return str(value)

    def _human_age(self, delta) -> str:
        seconds = max(int(delta.total_seconds()), 0)
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            mins = seconds // 60
            return f"{mins} min ago" if mins == 1 else f"{mins} mins ago"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
        days = seconds // 86400
        return f"{days} day ago" if days == 1 else f"{days} days ago"


class OwletMonitorApp(wx.App):
    def OnInit(self) -> bool:
        frame = OwletMonitorFrame()
        frame.Show()
        return True


if __name__ == "__main__":
    app = OwletMonitorApp(False)
    app.MainLoop()
