import asyncio
import json
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

import wx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pyowletapi.api import OwletAPI
from pyowletapi.exceptions import OwletError
from pyowletapi.sock import Sock


class OwletMonitorFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(parent=None, title="Owlet Monitor", size=(780, 520))
        panel = wx.Panel(self)

        self.status = wx.StaticText(panel, label="Idle")
        self.output = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
        )
        self.start_btn = wx.Button(panel, label="Start")
        self.stop_btn = wx.Button(panel, label="Stop")
        self.stop_btn.Disable()

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        button_row.Add(self.start_btn, flag=wx.RIGHT, border=8)
        button_row.Add(self.stop_btn)

        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(self.status, flag=wx.ALL | wx.EXPAND, border=10)
        layout.Add(button_row, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)
        layout.Add(self.output, proportion=1, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, border=10)
        panel.SetSizer(layout)

        self.poll_seconds = 10
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.start_btn.Bind(wx.EVT_BUTTON, self.on_start)
        self.stop_btn.Bind(wx.EVT_BUTTON, self.on_stop)
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def set_status(self, text: str) -> None:
        self.status.SetLabel(text)

    def append_output(self, text: str) -> None:
        self.output.AppendText(text + "\n")

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
            wx.CallAfter(self.append_output, traceback.format_exc())
            wx.CallAfter(self.set_status, "Crashed")
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

            wx.CallAfter(
                self.append_output,
                f"Connected. Found {len(socks)} device(s). Polling every {self.poll_seconds}s.",
            )
            wx.CallAfter(self.set_status, "Running")

            while not self._stop_event.is_set():
                rendered = await self._poll_once(socks)
                wx.CallAfter(self.output.SetValue, rendered)
                for _ in range(self.poll_seconds):
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1)

        except (OwletError, KeyError, FileNotFoundError, json.JSONDecodeError) as err:
            wx.CallAfter(self.append_output, f"Error: {err}")
            wx.CallAfter(self.set_status, "Error")
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

    async def _poll_once(self, socks: dict[str, Sock]) -> str:
        lines: list[str] = []
        for serial, sock in socks.items():
            result = await sock.update_properties()
            props: dict[str, Any] = result["properties"]
            lines.append(f"Device: {sock.name} ({serial})")
            for key in sorted(props.keys()):
                lines.append(f"  {key}: {props[key]}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


class OwletMonitorApp(wx.App):
    def OnInit(self) -> bool:
        frame = OwletMonitorFrame()
        frame.Show()
        return True


if __name__ == "__main__":
    app = OwletMonitorApp(False)
    app.MainLoop()
