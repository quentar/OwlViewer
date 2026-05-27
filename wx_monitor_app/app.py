import asyncio
import json
import math
import sys
import threading
import traceback
from datetime import datetime, timezone
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
LINE_COLOR = wx.Colour(35, 117, 255)


class SparklinePanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.SetMinSize((120, 54))
        self.values: list[float] = []
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def set_values(self, values: list[float]) -> None:
        self.values = values
        self.Refresh()

    def _on_paint(self, _event: wx.PaintEvent) -> None:
        dc = wx.PaintDC(self)
        w, h = self.GetClientSize()
        dc.SetBrush(wx.Brush(wx.Colour(248, 252, 255)))
        dc.SetPen(wx.Pen(wx.Colour(230, 235, 242), 1))
        dc.DrawRectangle(0, 0, w, h)

        if not self.values:
            return

        vals = self.values
        vmin = min(vals)
        vmax = max(vals)
        if abs(vmax - vmin) < 1e-9:
            vmin -= 1.0
            vmax += 1.0

        axis_font = wx.Font(12, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        dc.SetFont(axis_font)
        dc.SetTextForeground(LINE_COLOR)
        dc.DrawText(f"{vmax:.1f}", 4, 2)
        min_label = f"{vmin:.1f}"
        tw, th = dc.GetTextExtent(min_label)
        dc.DrawText(min_label, 4, max(h - th - 2, 0))

        left_pad = max(tw + 12, 34)
        right_pad = 8
        top_pad = 6
        bottom_pad = 6
        usable_w = max(w - left_pad - right_pad, 1)
        usable_h = max(h - top_pad - bottom_pad, 1)

        n = len(vals)
        points: list[wx.Point] = []
        for i, value in enumerate(vals):
            if n == 1:
                x = left_pad + usable_w // 2
            else:
                x = int(left_pad + (usable_w * i / (n - 1)))
            ratio = (value - vmin) / (vmax - vmin)
            y = int(top_pad + usable_h - (ratio * usable_h))
            points.append(wx.Point(x, y))

        dc.SetPen(wx.Pen(LINE_COLOR, 2))
        if len(points) > 1:
            dc.DrawLines(points)
        dc.SetBrush(wx.Brush(LINE_COLOR))
        last = points[-1]
        dc.DrawCircle(last.x, last.y, 3)


class MetricTile(wx.Panel):
    def __init__(
        self,
        parent: wx.Window,
        label: str,
        alerts: list[dict[str, Any]],
        default_vocalize: bool,
        chart_enabled: bool,
    ) -> None:
        super().__init__(parent)
        self.SetMinSize((180, 150))
        self.SetBackgroundColour(wx.Colour(245, 246, 248))

        self.inner = wx.Panel(self)
        self.inner.SetBackgroundColour(LEVEL_COLORS["normal"])

        self.title = wx.StaticText(self.inner, label=label)
        self.title.SetFont(wx.Font(12, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self.title.SetForegroundColour(TITLE_COLOR)
        self.vocalize_cb = wx.CheckBox(self.inner, label="🔊")
        self.vocalize_cb.SetValue(default_vocalize)

        self.value = wx.StaticText(self.inner, label="--")
        self.value.SetForegroundColour(TEXT_COLOR)
        self._set_best_font("--")

        self.alert_rows: list[dict[str, Any]] = []
        alert_sizer = wx.BoxSizer(wx.VERTICAL)
        for alert in alerts:
            row = wx.BoxSizer(wx.HORIZONTAL)
            info = wx.StaticText(self.inner, label=self._alert_label(alert))
            info.SetForegroundColour(wx.Colour(70, 70, 70))
            row.Add(info, proportion=1, flag=wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, border=8)
            alert_sizer.Add(row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
            self.alert_rows.append({"name": str(alert.get("name", "alert")), "label": info})

        self.chart_enabled = chart_enabled
        self.chart: SparklinePanel | None = SparklinePanel(self.inner) if self.chart_enabled else None

        inner_layout = wx.BoxSizer(wx.VERTICAL)
        title_row = wx.BoxSizer(wx.HORIZONTAL)
        title_row.Add(self.title, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        title_row.Add(self.vocalize_cb, flag=wx.ALIGN_CENTER_VERTICAL)
        inner_layout.Add(title_row, flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, border=10)
        inner_layout.Add(self.value, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        inner_layout.Add(alert_sizer, proportion=0, flag=wx.EXPAND)
        if self.chart_enabled and self.chart is not None:
            inner_layout.Add(self.chart, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        self.inner.SetSizer(inner_layout)

        border = wx.BoxSizer(wx.VERTICAL)
        border.Add(self.inner, proportion=1, flag=wx.ALL | wx.EXPAND, border=3)
        self.SetSizer(border)

    def _alert_label(self, alert: dict[str, Any]) -> str:
        name = str(alert.get("name", "alert"))
        when = str(alert.get("when", "below"))
        threshold = alert.get("threshold", "?")
        consecutive = int(alert.get("consecutive", 1))
        return f"{name}: {when} {threshold} x{consecutive}"

    def set_value(self, value: str, level: str) -> None:
        display = value if level == "normal" else f"{value} ({level})"
        self._set_best_font(display)
        self.value.SetLabel(display)
        self.Layout()

    def set_alert_active_level(self, level: str) -> None:
        for row in self.alert_rows:
            is_active = row["name"].lower() == level
            row["label"].SetForegroundColour(wx.Colour(160, 0, 0) if is_active else wx.Colour(70, 70, 70))

    def set_alert_background(self, level: str, flash_on: bool = False) -> None:
        if level == "red" and flash_on:
            color = FLASH_RED
        else:
            color = LEVEL_COLORS.get(level, LEVEL_COLORS["normal"])
        self.inner.SetBackgroundColour(color)
        self.inner.Refresh()

    def set_chart_values(self, values: list[float]) -> None:
        if not self.chart_enabled or self.chart is None:
            return
        self.chart.set_values(values)

    def is_vocalize_enabled(self) -> bool:
        return self.vocalize_cb.GetValue()

    def _set_best_font(self, text: str) -> None:
        width = max(self.GetSize().GetWidth() - 24, 120)
        height = max(self.GetSize().GetHeight() - 120, 46)
        for point in range(62, 14, -1):
            font = wx.Font(point, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            self.value.SetFont(font)
            tw, th = self.value.GetTextExtent(text)
            if tw <= width and th <= height:
                return
        self.value.SetFont(wx.Font(14, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))


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
        self._grid_cols = 1

        self._rule_counts: dict[str, dict[str, int]] = {}
        self._current_levels: dict[str, str] = {}
        self._flashing_keys: set[str] = set()
        self._flash_on = False
        self._latest_props: dict[str, Any] = {}
        self._series: dict[str, list[float]] = {}

        defaults = self.layout_config.get("defaults", {})
        self.default_vocalize = bool(defaults.get("vocalize", True))
        self.default_history_size = int(defaults.get("history_size", 30))
        self.default_chart = bool(defaults.get("chart", False))
        self.poll_seconds = int(defaults.get("poll_interval_seconds", self.layout_config.get("poll_seconds", 10)))
        self.vocalize_master_enabled = bool(defaults.get("vocalize_master", False))

        self.vocalize_master_btn = wx.Button(
            panel,
            label="Vocalize: ON" if self.vocalize_master_enabled else "Vocalize: OFF",
        )
        self.interval_label = wx.StaticText(panel, label="Interval (s):")
        self.interval_ctrl = wx.SpinCtrl(panel, min=1, max=300, initial=self.poll_seconds)

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        button_row.Add(self.start_btn, flag=wx.RIGHT, border=8)
        button_row.Add(self.stop_btn)
        button_row.AddSpacer(12)
        button_row.Add(self.vocalize_master_btn)
        button_row.AddSpacer(12)
        button_row.Add(self.interval_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        button_row.Add(self.interval_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        self.tiles_grid = wx.GridSizer(rows=0, cols=1, vgap=8, hgap=8)
        for box in self.layout_config["boxes"]:
            prop = str(box["property"])
            label = str(box.get("label", prop))
            alerts = box.get("alerts", [])
            chart_enabled = bool(box.get("chart", self.default_chart))
            tile = MetricTile(
                panel,
                label=label,
                alerts=alerts,
                default_vocalize=self.default_vocalize,
                chart_enabled=chart_enabled,
            )
            self.tiles[prop] = tile
            self.box_config[prop] = box
            self.ordered_properties.append(prop)
            self._rule_counts[prop] = {}
            self._current_levels[prop] = "normal"
            self._series[prop] = []
            self.tiles_grid.Add(tile, proportion=1, flag=wx.EXPAND)

        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(self.status, flag=wx.ALL | wx.EXPAND, border=10)
        layout.Add(button_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        layout.Add(self.tiles_grid, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        panel.SetSizer(layout)

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.flash_timer = wx.Timer(self)
        self.ui_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_flash_timer, self.flash_timer)
        self.Bind(wx.EVT_TIMER, self._on_ui_timer, self.ui_timer)
        self.ui_timer.Start(1000)

        self.start_btn.Bind(wx.EVT_BUTTON, self.on_start)
        self.stop_btn.Bind(wx.EVT_BUTTON, self.on_stop)
        self.vocalize_master_btn.Bind(wx.EVT_BUTTON, self.on_toggle_vocalize_master)
        self.interval_ctrl.Bind(wx.EVT_SPINCTRL, self.on_interval_change)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_SIZE, self._on_resize)
        self._reflow_grid()

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
        self.ui_timer.Stop()
        event.Skip()

    def on_toggle_vocalize_master(self, _event: wx.CommandEvent) -> None:
        self.vocalize_master_enabled = not self.vocalize_master_enabled
        self.vocalize_master_btn.SetLabel(
            "Vocalize: ON" if self.vocalize_master_enabled else "Vocalize: OFF"
        )
        self._save_default_setting("vocalize_master", self.vocalize_master_enabled)
        self.set_status(
            "Vocalize enabled" if self.vocalize_master_enabled else "Vocalize disabled"
        )

    def on_interval_change(self, _event: wx.CommandEvent) -> None:
        self.poll_seconds = int(self.interval_ctrl.GetValue())
        self._save_default_setting("poll_interval_seconds", self.poll_seconds)
        self.set_status(f"Polling interval set to {self.poll_seconds}s")

    def _save_default_setting(self, key: str, value: Any) -> None:
        defaults = self.layout_config.setdefault("defaults", {})
        defaults[key] = value
        with LAYOUT_PATH.open("w", encoding="utf-8") as file:
            json.dump(self.layout_config, file, indent=2)
            file.write("\n")

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
        self._latest_props = props
        now = datetime.now()
        for prop in self.ordered_properties:
            config = self.box_config[prop]
            raw_value = props.get(prop)
            self._append_series(prop, raw_value)
            level = self._evaluate_alert_level(config, raw_value)
            self._current_levels[prop] = level
            tile = self.tiles[prop]
            tile.set_value(self._format_metric(config, raw_value, now), level)
            tile.set_alert_active_level(level)
            tile.set_chart_values(self._series[prop])

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

    def _append_series(self, prop: str, raw_value: Any) -> None:
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            return
        history_size = int(self.box_config[prop].get("history_size", self.default_history_size))
        series = self._series[prop]
        series.append(numeric)
        if len(series) > history_size:
            del series[0 : len(series) - history_size]

    def _apply_alert_backgrounds(self) -> None:
        for prop in self.ordered_properties:
            level = self._current_levels.get(prop, "normal")
            flash_on = self._flash_on and prop in self._flashing_keys
            self.tiles[prop].set_alert_background(level, flash_on)

    def _on_flash_timer(self, _event: wx.TimerEvent) -> None:
        self._flash_on = not self._flash_on
        self._apply_alert_backgrounds()

    def _on_ui_timer(self, _event: wx.TimerEvent) -> None:
        if not self._latest_props:
            return
        self._refresh_dynamic_display(datetime.now())

    def _refresh_dynamic_display(self, now: datetime) -> None:
        for prop in self.ordered_properties:
            config = self.box_config[prop]
            if config.get("type") not in {"epoch_with_age", "refreshed_age_seconds"}:
                continue
            raw_value = self._latest_props.get(prop)
            level = self._current_levels.get(prop, "normal")
            self.tiles[prop].set_value(self._format_metric(config, raw_value, now), level)

    def _on_resize(self, event: wx.SizeEvent) -> None:
        self._reflow_grid()
        event.Skip()

    def _reflow_grid(self) -> None:
        n = len(self.ordered_properties)
        if n == 0:
            return

        available = self.GetClientSize()
        best_cols = 1
        best_score = -1.0
        target_ratio = 1.35
        for cols in range(1, n + 1):
            rows = math.ceil(n / cols)
            cell_w = max((available.width - (cols - 1) * 8 - 40) / cols, 1)
            cell_h = max((available.height - (rows - 1) * 8 - 130) / rows, 1)
            area = cell_w * cell_h
            ratio_penalty = abs((cell_w / cell_h) - target_ratio)
            score = area - (ratio_penalty * 5000)
            if score > best_score:
                best_score = score
                best_cols = cols

        if best_cols != self._grid_cols:
            self._grid_cols = best_cols
            self.tiles_grid.SetCols(best_cols)
            self.tiles_grid.SetRows(math.ceil(n / best_cols))
            self.Layout()

    def _evaluate_alert_level(self, config: dict[str, Any], value: Any) -> str:
        alerts = config.get("alerts") or []
        if not alerts:
            return "normal"

        prop = str(config["property"])
        if prop not in self._rule_counts:
            self._rule_counts[prop] = {}

        level_rank = {"normal": 0, "yellow": 1, "red": 2}
        active = "normal"

        for idx, alert in enumerate(alerts):
            key = str(alert.get("name", f"rule_{idx}"))
            try:
                numeric_value = float(value)
                threshold = float(alert.get("threshold"))
            except (TypeError, ValueError):
                self._rule_counts[prop][key] = 0
                continue

            cond = False
            when = str(alert.get("when", "below"))
            if when == "below":
                cond = numeric_value < threshold
            elif when == "above":
                cond = numeric_value > threshold

            self._rule_counts[prop][key] = self._rule_counts[prop].get(key, 0) + 1 if cond else 0
            needed = int(alert.get("consecutive", 1))
            if self._rule_counts[prop][key] >= needed:
                level = str(alert.get("name", "normal")).lower()
                if level_rank.get(level, 0) > level_rank.get(active, 0):
                    active = level

        return active

    def _should_flash_red(self, config: dict[str, Any]) -> bool:
        alerts = config.get("alerts") or []
        for rule in alerts:
            if str(rule.get("name", "")).lower() == "red" and bool(rule.get("flash", False)):
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
                return f"{age}\n{dt.strftime('%H:%M:%S')}"
            except (ValueError, TypeError, OSError):
                return str(value)
        if value_type == "refreshed_age_seconds":
            try:
                dt = datetime.strptime(str(value), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
                age_s = max(int((datetime.now(timezone.utc) - dt).total_seconds()), 0)
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
