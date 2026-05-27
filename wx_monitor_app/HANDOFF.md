# Owlet wx GUI Handoff

This is a deeper technical handoff for continuing development safely.

## Quick orientation

- Entry point: `wx_monitor_app/app.py`
- Main config/state file: `wx_monitor_app/layout.json`
- Startup backup snapshot: `wx_monitor_app/layout.settings-backup.json`
- Credentials: `login.json` (repo root)

The app is a wxPython dashboard over `pyowletapi` that polls one Owlet sock, renders tiles, evaluates alert rules, and optionally speaks values/alerts.

## Current behavior summary

- Polls Owlet every `poll_interval_seconds` (header spin control; persisted to config).
- UI timer refreshes dynamic time displays every 1 second without API calls.
- Alert state per tile (`normal`, `yellow`, `red`) with optional flashing for `red`.
- Optional sparkline chart per tile with last N values and min/max axis labels.
- Per-tile `🔊` checkbox + global `Vocalize ON/OFF` gate.
- Speech is serialized through a queue (no overlapping phrases).
- Settings are persisted live to `layout.json` and backed up at app startup.

## Threading model and event flow

### GUI thread (wx main loop)

Owns all controls and layout. Must be the only place touching widgets.

- `ui_timer` (`1s`): updates relative-time fields.
- `flash_timer` (`500ms` when needed): toggles red flash background.
- Resize events: recompute responsive grid columns.
- Button/checkbox events: update settings and persist them.

### Poll worker thread

Created on Start. Runs `asyncio.run(_monitor_loop())`.

Loop shape:
1. Authenticate with Owlet API.
2. Pull properties.
3. `wx.CallAfter(_apply_metrics, props)` to marshal data back to GUI thread.
4. Sleep `poll_seconds` in 1-second chunks (so stop is responsive).

### Speech worker thread

Runs continuously, consuming a FIFO queue.

- Messages are enqueued in desired priority order each poll cycle.
- Worker executes `say` synchronously per item to avoid overlaps.
- Global stop pushes a sentinel and exits worker loop.

## Key classes/functions in `app.py`

## `SparklinePanel`

- Lightweight drawing panel for value trends.
- Stores numeric history in `self.values`.
- Paint logic:
  - draws background
  - computes `vmin`, `vmax`
  - draws max/min labels in blue at left gutter
  - shifts chart area right so line never overlaps labels
  - draws polyline and endpoint marker

## `MetricTile`

A reusable tile component:
- title + top-right vocalize checkbox
- large auto-sized value text
- alert rule rows
- optional chart

Important methods:
- `set_value(value, level)`: updates large text and appends `(yellow/red)` when active.
- `set_alert_active_level(level)`: highlights matching rule text.
- `set_alert_background(level, flash_on)`: normal/yellow/red tile fill + flash.
- `is_vocalize_enabled()`: per-tile speech gate.

## `OwletMonitorFrame`

Main window and controller.

### Construction path

1. `_load_layout_config()` reads JSON.
2. `_backup_settings_file()` copies `layout.json` -> `layout.settings-backup.json`.
3. Reads defaults (`poll interval`, vocalization engine/interval, etc.).
4. Builds header controls (Start/Stop, global Vocalize button, interval spinner).
5. Builds one `MetricTile` per `boxes[]` entry.
6. Binds tile vocalize checkbox events to persist changes.
7. Starts timers and event bindings.

### Data update path

`_apply_metrics(props)` is the core per-poll update:
1. Save latest raw props (`self._latest_props`).
2. For each configured tile/property:
   - append numeric value to series
   - evaluate alert state
   - format display value
   - refresh chart
   - build optional speech message
3. Sort speech messages by `(vocalization_priority, box order)` and enqueue.
4. Update flashing set for red alerts.

### Alert evaluation

`_evaluate_alert_level(config, value)`:
- Supports multiple rules per tile.
- Supports `when: below` and `when: above`.
- Tracks independent consecutive counters per rule.
- Chooses highest active severity by rank (`normal < yellow < red`).

### Vocalization decision

`_build_vocalization_message(...)` returns a phrase or `None`.

Hard gates:
- `vocalization_engine != none`
- global `vocalize_master_enabled == True`
- tile checkbox enabled

Then:
- if level is `yellow`/`red`: speak every poll cycle
- else: speak only when `vocalization_interval_seconds` elapsed for that field

Phrase format:
- `vocalization_short + vocalization_value`
- units intentionally omitted for short speech
- fallback short name is tile label/property

## Settings model (`layout.json`)

## `defaults`

- `history_size`: default chart point count
- `chart`: default per-tile chart enable
- `vocalize`: default tile checkbox state (unless box override)
- `vocalize_master`: startup state of global ON/OFF
- `poll_interval_seconds`: polling interval used by app
- `vocalization_engine`: `macos_say` or `none`
- `vocalization_interval_seconds`: cadence for non-alert speech

## Per-box keys

Required:
- `property`
- `label`
- `type`

Optional:
- `chart`
- `history_size`
- `vocalize` (per-box default override)
- `vocalization_short`
- `vocalization_priority` (lower speaks first)
- `alerts` list

Alert item:
- `name` (`yellow`, `red`, etc.)
- `when` (`below` or `above`)
- `threshold`
- `consecutive`
- `flash` (optional)

## Persistence behavior

- Header interval change -> writes `defaults.poll_interval_seconds`.
- Global vocalize toggle -> writes `defaults.vocalize_master`.
- Tile `🔊` toggle -> writes that tile’s `vocalize`.
- Writes happen immediately via `_save_layout_config()`.
- On each app start, latest settings are copied to backup file.

## Supported value types

Implemented in `_format_metric()`:
- `bpm`
- `percent`
- `minutes_duration`
- `epoch_with_age`
- `refreshed_age_seconds`
- `int`
- fallback: `str(value)`

If you add a new type, update both:
- `_format_metric()` for display
- `_vocalization_value()` for spoken numeric form

## Known limitations

- `_poll_once()` currently reads only first sock (`next(iter(...))`).
- `macos_say` engine is platform-specific.
- No GUI editor for alert rules; JSON edits required.
- Speech queue is in-memory only (no audit log).

## Common extension recipes

## Add a new tile

1. Add a new object in `boxes[]`.
2. Set `property`, `label`, `type`.
3. Optional: set `chart`, `alerts`, `vocalization_short`, `vocalization_priority`.
4. Restart app.

## Add a new alert policy

1. Add alert entries under tile `alerts`.
2. Choose `when`, `threshold`, `consecutive`.
3. Add `flash: true` for red-style flashing.

## Add another speech engine

1. Add engine value in config (`vocalization_engine`).
2. Extend `_speech_worker()` engine branch.
3. Keep worker synchronous per phrase to preserve ordering.

## Convert to multi-device dashboard

- Replace `_poll_once()` to return per-device groups.
- Either:
  - duplicate tile sets per device, or
  - prefix labels with device name and flatten keys.

## Run

```bash
cd /Users/peterv/lab/ailab/owletmon/pyowletapi
source .venv/bin/activate
python3 wx_monitor_app/app.py
```
