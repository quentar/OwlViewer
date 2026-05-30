import asyncio
import json
import math
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import wx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
LAYOUT_PATH = PROJECT_ROOT / "wx_monitor_app" / "layout.json"
LAYOUT_BACKUP_PATH = PROJECT_ROOT / "wx_monitor_app" / "layout.settings-backup.json"
ICON_PATH = PROJECT_ROOT / "wx_monitor_app" / "icon.jpeg"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pyowletapi.api import OwletAPI
from pyowletapi.exceptions import OwletError
from pyowletapi.sock import Sock


BOX_BACKGROUND_COLOR = wx.Colour(255, 255, 255)
LEVEL_COLORS = {
    "normal": BOX_BACKGROUND_COLOR,
    "yellow": wx.Colour(255, 247, 204),
    "red": wx.Colour(255, 213, 213),
}
FLASH_RED = wx.Colour(255, 120, 120)
TEXT_COLOR = wx.Colour(0, 0, 0)
TITLE_COLOR = wx.Colour(35, 35, 35)
LINE_COLOR = wx.Colour(35, 117, 255)


class SparklinePanel(wx.Panel):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.SetMinSize((120, 54))
        self.values: list[float] = []
        self.background_color = BOX_BACKGROUND_COLOR
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def set_values(self, values: list[float]) -> None:
        self.values = values
        self.Refresh()

    def set_background_color(self, color: wx.Colour) -> None:
        self.background_color = color
        self.Refresh()

    def _on_paint(self, _event: wx.PaintEvent) -> None:
        dc = wx.PaintDC(self)
        w, h = self.GetClientSize()
        dc.SetBrush(wx.Brush(self.background_color))
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
        value_font_boost: float = 1.0,
    ) -> None:
        super().__init__(parent)
        self.SetMinSize((180, 150))
        self.value_font_boost = value_font_boost
        self.text_color = TEXT_COLOR
        self.normal_bg_color = BOX_BACKGROUND_COLOR
        self.SetBackgroundColour(wx.Colour(245, 246, 248))

        self.inner = wx.Panel(self)
        self.inner.SetBackgroundColour(BOX_BACKGROUND_COLOR)

        self.title = wx.StaticText(self.inner, label=label)
        self.title.SetFont(wx.Font(12, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self.title.SetForegroundColour(TEXT_COLOR)
        self.vocalize_cb = wx.CheckBox(self.inner, label="🔊")
        self.vocalize_cb.SetValue(default_vocalize)
        self.alarm_vocalize_cb = wx.CheckBox(self.inner, label="🔔")
        self.alarm_vocalize_cb.SetValue(default_vocalize)

        self.value = wx.StaticText(self.inner, label="--")
        self.value.SetForegroundColour(TEXT_COLOR)
        self._set_best_font("--")

        self.alert_rows: list[dict[str, Any]] = []
        alert_sizer = wx.BoxSizer(wx.VERTICAL)
        for alert in alerts:
            row = wx.BoxSizer(wx.HORIZONTAL)
            info = wx.StaticText(self.inner, label=self._alert_label(alert))
            info.SetForegroundColour(TEXT_COLOR)
            row.Add(info, proportion=1, flag=wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, border=8)
            alert_sizer.Add(row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
            self.alert_rows.append({"name": str(alert.get("name", "alert")), "label": info})

        self.chart_enabled = chart_enabled
        self.chart: SparklinePanel | None = SparklinePanel(self.inner) if self.chart_enabled else None

        inner_layout = wx.BoxSizer(wx.VERTICAL)
        title_row = wx.BoxSizer(wx.HORIZONTAL)
        title_row.Add(self.title, proportion=1, flag=wx.ALIGN_CENTER_VERTICAL)
        title_row.Add(self.vocalize_cb, flag=wx.ALIGN_CENTER_VERTICAL)
        title_row.AddSpacer(6)
        title_row.Add(self.alarm_vocalize_cb, flag=wx.ALIGN_CENTER_VERTICAL)
        inner_layout.Add(title_row, flag=wx.LEFT | wx.RIGHT | wx.TOP | wx.EXPAND, border=10)
        inner_layout.Add(self.value, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        inner_layout.Add(alert_sizer, proportion=0, flag=wx.EXPAND)
        if self.chart_enabled and self.chart is not None:
            inner_layout.Add(self.chart, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        self.inner.SetSizer(inner_layout)

        border = wx.BoxSizer(wx.VERTICAL)
        border.Add(self.inner, proportion=1, flag=wx.ALL | wx.EXPAND, border=3)
        self.SetSizer(border)

    def apply_colors(self, text_color: wx.Colour, normal_bg_color: wx.Colour) -> None:
        self.text_color = text_color
        self.normal_bg_color = normal_bg_color
        self.title.SetForegroundColour(text_color)
        self.value.SetForegroundColour(text_color)
        self.vocalize_cb.SetForegroundColour(text_color)
        self.alarm_vocalize_cb.SetForegroundColour(text_color)
        for row in self.alert_rows:
            row["label"].SetForegroundColour(text_color)
        if self.chart is not None:
            self.chart.set_background_color(normal_bg_color)
        self.Refresh()

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
            row["label"].SetForegroundColour(self.text_color)

    def set_alert_background(self, level: str, flash_on: bool = False) -> None:
        if level == "red" and flash_on:
            color = FLASH_RED
        elif level == "normal":
            color = self.normal_bg_color
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

    def is_alarm_vocalize_enabled(self) -> bool:
        return self.alarm_vocalize_cb.GetValue()

    def _set_best_font(self, text: str) -> None:
        width = max(self.GetSize().GetWidth() - 24, 120)
        height = max(self.GetSize().GetHeight() - 120, 46)
        start_point = max(int(62 * self.value_font_boost), 62)
        for point in range(start_point, 14, -1):
            font = wx.Font(point, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            self.value.SetFont(font)
            tw, th = self.value.GetTextExtent(text)
            if tw <= width and th <= height:
                return
        self.value.SetFont(wx.Font(14, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))


class OwletMonitorFrame(wx.Frame):
    def __init__(self) -> None:
        self.layout_config = self._load_layout_config()
        self._backup_settings_file()
        title = str(self.layout_config.get("title", "Owlet Monitor"))
        super().__init__(parent=None, title=title, size=(1120, 820))

        root_panel = wx.Panel(self)
        self.notebook = wx.Notebook(root_panel)
        monitor_panel = wx.Panel(self.notebook)
        self.monitor_panel = monitor_panel
        settings_panel = wx.Panel(self.notebook)
        self.notebook.AddPage(monitor_panel, "Monitor")
        self.notebook.AddPage(settings_panel, "Settings")

        self.status = wx.StaticText(monitor_panel, label="Idle")
        self.start_btn = wx.Button(monitor_panel, label="Start")
        self.stop_btn = wx.Button(monitor_panel, label="Stop")
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
        self._alarm_test_until: float = 0.0
        self._last_reconnect_utc: datetime | None = None
        self._successful_requests_since_reconnect = 0
        self._successful_requests_total = 0

        defaults = self.layout_config.get("defaults", {})
        self.default_vocalize = bool(defaults.get("vocalize", True))
        self.chart_values_count = int(defaults.get("chart_values_count", defaults.get("history_size", 30)))
        self.default_chart = bool(defaults.get("chart", False))
        self.poll_seconds = int(defaults.get("poll_interval_seconds", self.layout_config.get("poll_seconds", 10)))
        self.vocalize_master_enabled = bool(defaults.get("vocalize_master", False))
        self.vocalization_engine = str(defaults.get("vocalization_engine", "macos_say"))
        self.vocalization_interval_seconds = int(defaults.get("vocalization_interval_seconds", 10))
        self.reconnect_stale_seconds = int(defaults.get("reconnect_stale_seconds", 180))
        self.announce_alarm_ended = bool(defaults.get("announce_alarm_ended", True))
        self.start_after_launch = bool(defaults.get("start_after_launch", True))
        self.start_maximized = bool(defaults.get("start_maximized", False))
        self.night_colors_enabled = bool(defaults.get("night_colors", False))
        self.big_box_value_font_boost = float(defaults.get("big_box_value_font_boost", 1.55))
        self.big_width_scale = float(defaults.get("big_width_scale", 1.45))
        self.big_height_scale = float(defaults.get("big_height_scale", 1.60))
        self.normal_width_scale = float(defaults.get("normal_width_scale", 1.00))
        self.normal_height_scale = float(defaults.get("normal_height_scale", 1.00))
        self.compact_width_scale = float(defaults.get("compact_width_scale", 0.72))
        self.compact_height_scale = float(defaults.get("compact_height_scale", 0.55))
        self.window_x = int(defaults.get("window_x", -1))
        self.window_y = int(defaults.get("window_y", -1))
        self.window_w = int(defaults.get("window_w", 1120))
        self.window_h = int(defaults.get("window_h", 820))
        self._last_vocalized_at: dict[str, float] = {}
        self._speech_queue: queue.Queue[str] = queue.Queue()
        self._speech_stop_event = threading.Event()
        self._speech_thread = threading.Thread(target=self._speech_worker, daemon=True)
        self._speech_thread.start()

        self.vocalize_master_btn = wx.Button(
            monitor_panel,
            label="Vocalize: ON" if self.vocalize_master_enabled else "Vocalize: OFF",
        )
        self.interval_label = wx.StaticText(monitor_panel, label="Interval (s):")
        self.interval_ctrl = wx.SpinCtrl(monitor_panel, min=1, max=300, initial=self.poll_seconds)
        self.vocal_interval_label = wx.StaticText(monitor_panel, label="Vocal Int (s):")
        self.vocal_interval_ctrl = wx.SpinCtrl(monitor_panel, min=1, max=3600, initial=self.vocalization_interval_seconds)
        self.alarm_test_btn = wx.Button(monitor_panel, label="Alarm Test")
        self.night_colors_btn = wx.Button(
            monitor_panel,
            label="Night Colors: ON" if self.night_colors_enabled else "Night Colors: OFF",
        )

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        button_row.Add(self.start_btn, flag=wx.RIGHT, border=8)
        button_row.Add(self.stop_btn)
        button_row.AddSpacer(12)
        button_row.Add(self.vocalize_master_btn)
        button_row.AddSpacer(12)
        button_row.Add(self.interval_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        button_row.Add(self.interval_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        button_row.AddSpacer(12)
        button_row.Add(self.vocal_interval_label, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        button_row.Add(self.vocal_interval_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        button_row.AddSpacer(12)
        button_row.Add(self.night_colors_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        button_row.AddStretchSpacer(1)
        button_row.Add(self.alarm_test_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        max_row = 1
        for box in self.layout_config["boxes"]:
            try:
                max_row = max(max_row, int(box.get("row", 1)))
            except (TypeError, ValueError):
                max_row = max(max_row, 1)

        row_height_sums: dict[int, float] = {i: 0.0 for i in range(1, max_row + 1)}
        row_counts: dict[int, int] = {i: 0 for i in range(1, max_row + 1)}
        for box in self.layout_config["boxes"]:
            try:
                row_idx = int(box.get("row", 1))
            except (TypeError, ValueError):
                row_idx = 1
            row_idx = max(1, min(row_idx, max_row))
            size_class = str(box.get("size_class", "normal")).lower()
            _, h_scale = self._class_scales(size_class)
            row_height_sums[row_idx] += h_scale
            row_counts[row_idx] += 1

        row_height_weights: dict[int, float] = {}
        for i in range(1, max_row + 1):
            if row_counts[i] == 0:
                row_height_weights[i] = 1.0
            else:
                row_height_weights[i] = max(0.35, row_height_sums[i] / row_counts[i])

        self.row_sizers: dict[int, wx.BoxSizer] = {}
        self.tiles_rows = wx.BoxSizer(wx.VERTICAL)
        for row_idx in range(1, max_row + 1):
            row_sizer = wx.BoxSizer(wx.HORIZONTAL)
            self.row_sizers[row_idx] = row_sizer
            bottom_border = 10 if row_idx < max_row else 0
            row_prop = max(1, int(round(row_height_weights[row_idx] * 100)))
            self.tiles_rows.Add(row_sizer, proportion=row_prop, flag=wx.EXPAND | wx.BOTTOM, border=bottom_border)

        for box in self.layout_config["boxes"]:
            prop = str(box["property"])
            label = str(box.get("label", prop))
            alerts = box.get("alerts", [])
            chart_enabled = bool(box.get("chart", self.default_chart))
            tile_vocalize_default = bool(box.get("vocalize", self.default_vocalize))
            tile_alarm_vocalize_default = bool(box.get("vocalize_alert", tile_vocalize_default))
            size_class = str(box.get("size_class", "normal")).lower()
            value_font_boost = (
                self.big_box_value_font_boost if size_class == "big" else float(box.get("value_font_boost", 1.0))
            )
            width_scale, height_scale = self._class_scales(size_class)
            tile = MetricTile(
                monitor_panel,
                label=label,
                alerts=alerts,
                default_vocalize=tile_vocalize_default,
                chart_enabled=chart_enabled,
                value_font_boost=value_font_boost,
            )
            tile.alarm_vocalize_cb.SetValue(tile_alarm_vocalize_default)
            self.tiles[prop] = tile
            self.box_config[prop] = box
            self.ordered_properties.append(prop)
            self._rule_counts[prop] = {}
            self._current_levels[prop] = "normal"
            self._series[prop] = []
            tile.vocalize_cb.Bind(
                wx.EVT_CHECKBOX,
                lambda event, key=prop: self.on_tile_vocalize_change(event, key),
            )
            tile.alarm_vocalize_cb.Bind(
                wx.EVT_CHECKBOX,
                lambda event, key=prop: self.on_tile_alarm_vocalize_change(event, key),
            )
            try:
                row = int(box.get("row", 1))
            except (TypeError, ValueError):
                row = 1
            row = max(1, min(row, max_row))
            width_prop = max(1, int(round(width_scale * 100)))
            if size_class == "compact":
                compact_max_h = max(110, int(220 * height_scale))
                tile.SetMaxSize((-1, compact_max_h))
                tile.SetMinSize((180, 95))
                self.row_sizers[row].Add(tile, proportion=width_prop, flag=wx.ALL | wx.EXPAND | wx.ALIGN_TOP, border=6)
            else:
                tile.SetMaxSize((-1, -1))
                self.row_sizers[row].Add(tile, proportion=width_prop, flag=wx.ALL | wx.EXPAND, border=6)

        monitor_layout = wx.BoxSizer(wx.VERTICAL)
        monitor_layout.Add(self.status, flag=wx.ALL | wx.EXPAND, border=10)
        monitor_layout.Add(button_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        monitor_layout.Add(self.tiles_rows, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        monitor_panel.SetSizer(monitor_layout)

        self.settings_poll_ctrl = wx.SpinCtrl(settings_panel, min=1, max=300, initial=self.poll_seconds)
        self.settings_vocal_interval_ctrl = wx.SpinCtrl(
            settings_panel, min=1, max=3600, initial=self.vocalization_interval_seconds
        )
        self.settings_reconnect_ctrl = wx.SpinCtrl(
            settings_panel, min=10, max=3600, initial=self.reconnect_stale_seconds
        )
        self.settings_chart_count_ctrl = wx.SpinCtrl(
            settings_panel, min=5, max=300, initial=self.chart_values_count
        )
        self.settings_engine_ctrl = wx.Choice(settings_panel, choices=["macos_say", "none"])
        self.settings_engine_ctrl.SetStringSelection(
            self.vocalization_engine if self.vocalization_engine in {"macos_say", "none"} else "macos_say"
        )
        self.settings_master_cb = wx.CheckBox(settings_panel, label="Global Vocalize Enabled")
        self.settings_master_cb.SetValue(self.vocalize_master_enabled)
        self.settings_alarm_ended_cb = wx.CheckBox(settings_panel, label="Announce Alarm Ended")
        self.settings_alarm_ended_cb.SetValue(self.announce_alarm_ended)
        self.settings_start_after_launch_cb = wx.CheckBox(settings_panel, label="Start Monitor After Launch")
        self.settings_start_after_launch_cb.SetValue(self.start_after_launch)
        self.settings_start_maximized_cb = wx.CheckBox(settings_panel, label="Start Maximized")
        self.settings_start_maximized_cb.SetValue(self.start_maximized)
        self.settings_apply_btn = wx.Button(settings_panel, label="Apply Settings")
        self.debug_reconnect_label = wx.StaticText(settings_panel, label="Last Reconnection: never")
        self.debug_success_since_label = wx.StaticText(settings_panel, label="Successful Requests (since reconnect): 0")
        self.debug_success_total_label = wx.StaticText(settings_panel, label="Successful Requests (total): 0")

        settings_grid = wx.FlexGridSizer(18, 2, 6, 10)
        settings_grid.Add(wx.StaticText(settings_panel, label="Poll Interval (s):"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_poll_ctrl, flag=wx.EXPAND)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="How often API data is fetched. Lower = faster updates, more network traffic.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.Add(wx.StaticText(settings_panel, label="Vocal Interval (s):"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_vocal_interval_ctrl, flag=wx.EXPAND)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="Gap between non-alert spoken updates for each enabled tile.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.Add(wx.StaticText(settings_panel, label="Reconnect Stale (s):"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_reconnect_ctrl, flag=wx.EXPAND)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="If 'Last Refreshed' age exceeds this, monitor reconnects automatically.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.Add(wx.StaticText(settings_panel, label="Chart Values Count:"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_chart_count_ctrl, flag=wx.EXPAND)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="Number of recent values kept for charts and movement average.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.Add(wx.StaticText(settings_panel, label="Vocalization Engine:"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_engine_ctrl, flag=wx.EXPAND)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="Speech backend. 'macos_say' uses macOS voice; 'none' disables speech output.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.Add(wx.StaticText(settings_panel, label="Vocalize Master:"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_master_cb, flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="Global speech gate for non-alert messages. Active alerts can still speak.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.Add(wx.StaticText(settings_panel, label="Announce Alarm Ended:"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_alarm_ended_cb, flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="When alert clears, speaks '<name> back to <value>' if enabled.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.Add(wx.StaticText(settings_panel, label="Start After Launch:"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_start_after_launch_cb, flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="Automatically presses Start when the app window opens.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.Add(wx.StaticText(settings_panel, label="Start Maximized:"), flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(self.settings_start_maximized_cb, flag=wx.ALIGN_CENTER_VERTICAL)
        settings_grid.Add(wx.StaticText(settings_panel, label=""), flag=wx.EXPAND)
        settings_grid.Add(
            wx.StaticText(
                settings_panel,
                label="If enabled, launches maximized; otherwise restores last saved window position/size.",
            ),
            flag=wx.EXPAND,
        )
        settings_grid.AddGrowableCol(1, 1)

        settings_layout = wx.BoxSizer(wx.VERTICAL)
        settings_layout.Add(settings_grid, flag=wx.ALL | wx.EXPAND, border=16)
        settings_layout.Add(self.settings_apply_btn, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=16)
        settings_layout.Add(wx.StaticLine(settings_panel), flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=16)
        settings_layout.Add(wx.StaticText(settings_panel, label="Debug Info"), flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=16)
        settings_layout.Add(self.debug_reconnect_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=16)
        settings_layout.Add(self.debug_success_since_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=16)
        settings_layout.Add(self.debug_success_total_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=16)
        settings_layout.AddStretchSpacer(1)
        settings_panel.SetSizer(settings_layout)

        root_layout = wx.BoxSizer(wx.VERTICAL)
        root_layout.Add(self.notebook, proportion=1, flag=wx.EXPAND)
        root_panel.SetSizer(root_layout)

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
        self.vocal_interval_ctrl.Bind(wx.EVT_SPINCTRL, self.on_vocal_interval_change)
        self.night_colors_btn.Bind(wx.EVT_BUTTON, self.on_toggle_night_colors)
        self.alarm_test_btn.Bind(wx.EVT_BUTTON, self.on_alarm_test)
        self.settings_apply_btn.Bind(wx.EVT_BUTTON, self.on_apply_settings_tab)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_SIZE, self._on_resize)
        self.Bind(wx.EVT_MOVE, self._on_move)
        self._apply_night_colors()
        self._reflow_grid()
        wx.CallAfter(self._apply_initial_window_state)
        if self.start_after_launch:
            wx.CallAfter(self.on_start, wx.CommandEvent())

    def _apply_initial_window_state(self) -> None:
        if self.start_maximized:
            self.Maximize(True)
        elif self.window_x >= 0 and self.window_y >= 0:
            self.SetPosition((self.window_x, self.window_y))
            self.SetSize((self.window_w, self.window_h))
        self.Layout()
        self._reflow_grid()

    def _load_layout_config(self) -> dict[str, Any]:
        with LAYOUT_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if "boxes" not in data or not isinstance(data["boxes"], list):
            raise ValueError(f"Invalid layout config: missing boxes list in {LAYOUT_PATH}")
        return data

    def _backup_settings_file(self) -> None:
        try:
            shutil.copy2(LAYOUT_PATH, LAYOUT_BACKUP_PATH)
        except OSError:
            pass

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
        self._persist_window_state()
        self._request_stop()
        self.ui_timer.Stop()
        self._stop_speech_worker()
        event.Skip()

    def on_toggle_vocalize_master(self, _event: wx.CommandEvent) -> None:
        self.vocalize_master_enabled = not self.vocalize_master_enabled
        self.vocalize_master_btn.SetLabel(
            "Vocalize: ON" if self.vocalize_master_enabled else "Vocalize: OFF"
        )
        self.settings_master_cb.SetValue(self.vocalize_master_enabled)
        self._save_default_setting("vocalize_master", self.vocalize_master_enabled)
        self.set_status(
            "Vocalize enabled" if self.vocalize_master_enabled else "Vocalize disabled"
        )

    def on_interval_change(self, _event: wx.CommandEvent) -> None:
        self.poll_seconds = int(self.interval_ctrl.GetValue())
        self.settings_poll_ctrl.SetValue(self.poll_seconds)
        self._save_default_setting("poll_interval_seconds", self.poll_seconds)
        self.set_status(f"Polling interval set to {self.poll_seconds}s")

    def on_vocal_interval_change(self, _event: wx.CommandEvent) -> None:
        self.vocalization_interval_seconds = int(self.vocal_interval_ctrl.GetValue())
        self.settings_vocal_interval_ctrl.SetValue(self.vocalization_interval_seconds)
        self._save_default_setting("vocalization_interval_seconds", self.vocalization_interval_seconds)
        self.set_status(f"Vocalization interval set to {self.vocalization_interval_seconds}s")

    def on_toggle_night_colors(self, _event: wx.CommandEvent) -> None:
        self.night_colors_enabled = not self.night_colors_enabled
        self.night_colors_btn.SetLabel(
            "Night Colors: ON" if self.night_colors_enabled else "Night Colors: OFF"
        )
        self._apply_night_colors()
        self._save_default_setting("night_colors", self.night_colors_enabled)
        self.set_status(
            "Night colors enabled" if self.night_colors_enabled else "Night colors disabled"
        )

    def _apply_night_colors(self) -> None:
        if self.night_colors_enabled:
            text_color = wx.Colour(255, 255, 255)
            box_bg_color = wx.Colour(0, 0, 0)
            page_bg = wx.Colour(0, 0, 0)
        else:
            text_color = wx.Colour(0, 0, 0)
            box_bg_color = wx.Colour(255, 255, 255)
            page_bg = wx.Colour(240, 240, 240)

        self.monitor_panel.SetBackgroundColour(page_bg)
        self.status.SetForegroundColour(text_color)
        self.interval_label.SetForegroundColour(text_color)
        self.vocal_interval_label.SetForegroundColour(text_color)

        for tile in self.tiles.values():
            tile.apply_colors(text_color, box_bg_color)

        self._apply_alert_backgrounds()
        self.Refresh()

    def on_alarm_test(self, _event: wx.CommandEvent) -> None:
        self._speak("Test alarm")
        self._alarm_test_until = time.time() + 5.0
        self._flashing_keys.update({"heart_rate", "oxygen_saturation"})
        if not self.flash_timer.IsRunning():
            self.flash_timer.Start(500)
        self._apply_alert_backgrounds()
        self.set_status("Alarm test sent")

    def on_apply_settings_tab(self, _event: wx.CommandEvent) -> None:
        self.poll_seconds = int(self.settings_poll_ctrl.GetValue())
        self.vocalization_interval_seconds = int(self.settings_vocal_interval_ctrl.GetValue())
        self.reconnect_stale_seconds = int(self.settings_reconnect_ctrl.GetValue())
        self.chart_values_count = int(self.settings_chart_count_ctrl.GetValue())
        self.vocalization_engine = str(self.settings_engine_ctrl.GetStringSelection())
        self.vocalize_master_enabled = bool(self.settings_master_cb.GetValue())
        self.announce_alarm_ended = bool(self.settings_alarm_ended_cb.GetValue())
        self.start_after_launch = bool(self.settings_start_after_launch_cb.GetValue())
        self.start_maximized = bool(self.settings_start_maximized_cb.GetValue())

        self.interval_ctrl.SetValue(self.poll_seconds)
        self.vocal_interval_ctrl.SetValue(self.vocalization_interval_seconds)
        self.vocalize_master_btn.SetLabel(
            "Vocalize: ON" if self.vocalize_master_enabled else "Vocalize: OFF"
        )

        self._save_default_setting("poll_interval_seconds", self.poll_seconds)
        self._save_default_setting("vocalization_interval_seconds", self.vocalization_interval_seconds)
        self._save_default_setting("reconnect_stale_seconds", self.reconnect_stale_seconds)
        self._save_default_setting("chart_values_count", self.chart_values_count)
        self._save_default_setting("vocalization_engine", self.vocalization_engine)
        self._save_default_setting("vocalize_master", self.vocalize_master_enabled)
        self._save_default_setting("announce_alarm_ended", self.announce_alarm_ended)
        self._save_default_setting("start_after_launch", self.start_after_launch)
        self._save_default_setting("start_maximized", self.start_maximized)
        if self.start_maximized:
            self.Maximize(True)
        self.set_status("Settings updated")

    def on_tile_vocalize_change(self, _event: wx.CommandEvent, prop: str) -> None:
        self.box_config[prop]["vocalize"] = self.tiles[prop].is_vocalize_enabled()
        self._save_layout_config()

    def on_tile_alarm_vocalize_change(self, _event: wx.CommandEvent, prop: str) -> None:
        self.box_config[prop]["vocalize_alert"] = self.tiles[prop].is_alarm_vocalize_enabled()
        self._save_layout_config()

    def _save_default_setting(self, key: str, value: Any) -> None:
        defaults = self.layout_config.setdefault("defaults", {})
        defaults[key] = value
        self._save_layout_config()

    def _save_layout_config(self) -> None:
        with LAYOUT_PATH.open("w", encoding="utf-8") as file:
            json.dump(self.layout_config, file, indent=2)
            file.write("\n")

    def _class_scales(self, size_class: str) -> tuple[float, float]:
        if size_class == "big":
            return self.big_width_scale, self.big_height_scale
        if size_class == "compact":
            return self.compact_width_scale, self.compact_height_scale
        return self.normal_width_scale, self.normal_height_scale

    def _request_stop(self) -> None:
        self._stop_event.set()
        self.start_btn.Enable()
        self.stop_btn.Disable()
        self._flashing_keys.clear()
        self.flash_timer.Stop()
        self._apply_alert_backgrounds()
        self.set_status("Stopped")

    def _stop_speech_worker(self) -> None:
        if self._speech_stop_event.is_set():
            return
        self._speech_stop_event.set()
        self._speech_queue.put("")

    def _run_monitor(self) -> None:
        try:
            asyncio.run(self._monitor_loop())
        except Exception:
            wx.CallAfter(self.set_error, traceback.format_exc().splitlines()[-1])
            wx.CallAfter(self.start_btn.Enable)
            wx.CallAfter(self.stop_btn.Disable)

    async def _monitor_loop(self) -> None:
        config = self._load_login_config()
        while not self._stop_event.is_set():
            api: OwletAPI | None = None
            try:
                wx.CallAfter(self.set_status, "Authenticating...")
                api = OwletAPI(config["region"], config["username"], config["password"])
                await api.authenticate()
                devices = await api.get_devices()
                socks = {device["device"]["dsn"]: Sock(api, device["device"]) for device in devices["response"]}
                if not socks:
                    raise OwletError("No devices found")
                self._last_reconnect_utc = datetime.now(timezone.utc)
                self._successful_requests_since_reconnect = 0
                wx.CallAfter(self._update_debug_info_labels)

                wx.CallAfter(self.set_status, f"Running ({len(socks)} device(s), polling every {self.poll_seconds}s)")
                reconnect_needed = False
                while not self._stop_event.is_set():
                    props = await self._poll_once(socks)
                    self._successful_requests_since_reconnect += 1
                    self._successful_requests_total += 1
                    wx.CallAfter(self._apply_metrics, props)
                    wx.CallAfter(self._update_debug_info_labels)
                    stale_age = self._last_updated_age_seconds(props)
                    if stale_age is not None and stale_age > self.reconnect_stale_seconds:
                        reconnect_needed = True
                        wx.CallAfter(
                            self.set_status,
                            f"Last refresh stale ({stale_age}s > {self.reconnect_stale_seconds}s). Reconnecting...",
                        )
                        break
                    for _ in range(self.poll_seconds):
                        if self._stop_event.is_set():
                            break
                        await asyncio.sleep(1)

                if reconnect_needed and not self._stop_event.is_set():
                    await asyncio.sleep(2)

            except (OwletError, KeyError, FileNotFoundError, json.JSONDecodeError, ValueError) as err:
                wx.CallAfter(self.set_status, f"Monitor error: {err}. Retrying...")
                if not self._stop_event.is_set():
                    await asyncio.sleep(3)
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

    def _last_updated_age_seconds(self, props: dict[str, Any]) -> int | None:
        raw = props.get("last_updated")
        if not raw:
            return None
        try:
            dt = datetime.strptime(str(raw), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return max(int((datetime.now(timezone.utc) - dt).total_seconds()), 0)
        except ValueError:
            return None

    def _apply_metrics(self, props: dict[str, Any]) -> None:
        previous_levels = dict(self._current_levels)
        self._latest_props = props
        now = datetime.now()
        speak_items: list[tuple[int, int, str]] = []
        for idx, prop in enumerate(self.ordered_properties):
            config = self.box_config[prop]
            raw_value = props.get(prop)
            self._append_series(prop, raw_value)
            level = self._evaluate_alert_level(config, raw_value)
            self._current_levels[prop] = level
            tile = self.tiles[prop]
            tile.set_value(self._format_metric(prop, config, raw_value, now), level)
            tile.set_alert_active_level(level)
            tile.set_chart_values(self._series[prop])
            msg = self._build_vocalization_message(prop, raw_value, previous_levels.get(prop, "normal"), level)
            if msg:
                priority = int(config.get("vocalization_priority", idx + 100))
                speak_items.append((priority, idx, msg))

        for _, _, message in sorted(speak_items, key=lambda item: (item[0], item[1])):
            self._speak(message)

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

    def _build_vocalization_message(self, prop: str, value: Any, prev_level: str, level: str) -> str | None:
        if self.vocalization_engine == "none":
            return None
        tile = self.tiles[prop]

        if prev_level in {"yellow", "red"} and level == "normal":
            if tile.is_alarm_vocalize_enabled() and self.announce_alarm_ended:
                self._last_vocalized_at[prop] = time.time()
                return self._vocalization_back_phrase(prop, value)
            return None

        if level in {"yellow", "red"}:
            if not tile.is_alarm_vocalize_enabled():
                return None
            message = self._vocalization_phrase(prop, value)
            self._last_vocalized_at[prop] = time.time()
            return message

        if not tile.is_vocalize_enabled():
            return None
        if not self.vocalize_master_enabled:
            return None

        now_ts = time.time()
        last_ts = self._last_vocalized_at.get(prop, 0.0)
        if now_ts - last_ts < self.vocalization_interval_seconds:
            return None
        message = self._vocalization_phrase(prop, value)
        self._last_vocalized_at[prop] = now_ts
        return message

    def _vocalization_phrase(self, prop: str, value: Any) -> str:
        box = self.box_config[prop]
        shortcut = str(box.get("vocalization_short", box.get("label", prop)))
        value_text = self._vocalization_value(box, value)
        return f"{shortcut} {value_text}".strip()

    def _vocalization_back_phrase(self, prop: str, value: Any) -> str:
        box = self.box_config[prop]
        shortcut = str(box.get("vocalization_short", box.get("label", prop)))
        value_text = self._vocalization_value(box, value)
        return f"{shortcut} back to {value_text}".strip()

    def _vocalization_value(self, box: dict[str, Any], value: Any) -> str:
        if value is None:
            return "--"
        value_type = str(box.get("type", "plain"))
        try:
            if value_type in {"bpm", "percent", "int"}:
                return str(int(float(value)))
            if value_type == "minutes_duration":
                total = int(float(value))
                return f"{total // 60} {total % 60}"
        except (TypeError, ValueError):
            return str(value)
        return str(value)

    def _speak(self, text: str) -> None:
        if not text:
            return
        self._speech_queue.put(text)

    def _speech_worker(self) -> None:
        while not self._speech_stop_event.is_set():
            try:
                text = self._speech_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._speech_stop_event.is_set():
                break
            if not text:
                continue

            if self.vocalization_engine == "macos_say":
                say_bin = shutil.which("say")
                if not say_bin:
                    continue
                try:
                    subprocess.run([say_bin, text], check=False)
                except OSError:
                    continue


    def _append_series(self, prop: str, raw_value: Any) -> None:
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            return
        history_size = int(self.box_config[prop].get("history_size", self.chart_values_count))
        series = self._series[prop]
        series.append(numeric)
        if len(series) > history_size:
            del series[0 : len(series) - history_size]

    def _apply_alert_backgrounds(self) -> None:
        now_ts = time.time()
        if self._alarm_test_until and now_ts >= self._alarm_test_until:
            self._alarm_test_until = 0.0
            self._flashing_keys.discard("heart_rate")
            self._flashing_keys.discard("oxygen_saturation")
        for prop in self.ordered_properties:
            level = self._current_levels.get(prop, "normal")
            test_flash_active = self._alarm_test_until > now_ts and prop in {"heart_rate", "oxygen_saturation"}
            flash_on = self._flash_on and (prop in self._flashing_keys or test_flash_active)
            if test_flash_active and level == "normal":
                level = "red"
            self.tiles[prop].set_alert_background(level, flash_on)

    def _on_flash_timer(self, _event: wx.TimerEvent) -> None:
        self._flash_on = not self._flash_on
        self._apply_alert_backgrounds()

    def _on_ui_timer(self, _event: wx.TimerEvent) -> None:
        if not self._latest_props:
            self._update_debug_info_labels()
            return
        now = datetime.now()
        self._refresh_dynamic_display(now)
        self._update_debug_info_labels()

    def _update_debug_info_labels(self) -> None:
        if self._last_reconnect_utc is None:
            reconnect_text = "never"
        else:
            age_seconds = max(int((datetime.now(timezone.utc) - self._last_reconnect_utc).total_seconds()), 0)
            reconnect_text = f"{self._last_reconnect_utc.strftime('%Y/%m/%d %H:%M:%S')} UTC ({age_seconds}s ago)"
        self.debug_reconnect_label.SetLabel(f"Last Reconnection: {reconnect_text}")
        self.debug_success_since_label.SetLabel(
            f"Successful Requests (since reconnect): {self._successful_requests_since_reconnect}"
        )
        self.debug_success_total_label.SetLabel(
            f"Successful Requests (total): {self._successful_requests_total}"
        )

    def _refresh_dynamic_display(self, now: datetime) -> None:
        for prop in self.ordered_properties:
            config = self.box_config[prop]
            if config.get("type") not in {"epoch_with_age", "refreshed_age_seconds"}:
                continue
            raw_value = self._latest_props.get(prop)
            level = self._current_levels.get(prop, "normal")
            self.tiles[prop].set_value(self._format_metric(prop, config, raw_value, now), level)

    def _on_resize(self, event: wx.SizeEvent) -> None:
        self._reflow_grid()
        self._persist_window_state()
        event.Skip()

    def _on_move(self, event: wx.MoveEvent) -> None:
        self._persist_window_state()
        event.Skip()

    def _persist_window_state(self) -> None:
        if self.IsMaximized():
            return
        pos = self.GetPosition()
        size = self.GetSize()
        self._save_default_setting("window_x", int(pos.x))
        self._save_default_setting("window_y", int(pos.y))
        self._save_default_setting("window_w", int(size.x))
        self._save_default_setting("window_h", int(size.y))

    def _reflow_grid(self) -> None:
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

    def _format_metric(self, prop: str, config: dict[str, Any], value: Any, now: datetime) -> str:
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
        if value_type == "movement_with_avg":
            current = int(float(value))
            series = self._series.get(prop, [])
            avg = sum(series) / len(series) if series else float(current)
            return f"{current}\navg {avg:.1f}"

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
        if ICON_PATH.exists():
            icon = wx.Icon(str(ICON_PATH), wx.BITMAP_TYPE_JPEG)
            if icon.IsOk():
                frame.SetIcon(icon)
                self.SetTopWindow(frame)
        frame.Show()
        return True


if __name__ == "__main__":
    app = OwletMonitorApp(False)
    app.MainLoop()
