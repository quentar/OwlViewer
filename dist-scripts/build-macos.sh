#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_VENV="${PROJECT_ROOT}/.venv-build"
APP_NAME="OwletMonitor"

cd "${PROJECT_ROOT}"

python3 -m venv "${BUILD_VENV}"
"${BUILD_VENV}/bin/python" -m pip install --upgrade pip
"${BUILD_VENV}/bin/python" -m pip install aiohttp certifi wxPython pyinstaller

"${BUILD_VENV}/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --windowed \
  --name "${APP_NAME}" \
  --icon "wx_monitor_app/owlmon.icns" \
  --add-data "wx_monitor_app/layout.json:wx_monitor_app" \
  --add-data "wx_monitor_app/icon.jpeg:wx_monitor_app" \
  --add-data "src:src" \
  "wx_monitor_app/app.py"

cat <<EOF

Built: ${PROJECT_ROOT}/dist/${APP_NAME}.app

Put login.json next to the .app before giving it to a user:
  ${PROJECT_ROOT}/dist/login.json
  ${PROJECT_ROOT}/dist/${APP_NAME}.app

EOF
