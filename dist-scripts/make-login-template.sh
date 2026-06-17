#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cat > "${PROJECT_ROOT}/dist/login.json.template" <<'EOF'
{
  "region": "world",
  "username": "you@example.com",
  "password": "your_password"
}
EOF

echo "Wrote ${PROJECT_ROOT}/dist/login.json.template"
