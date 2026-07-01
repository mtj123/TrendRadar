#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="/Users/mtjljx/Documents/Codex/2026-06-18/github-ai/.venv/bin/python"
ENV_FILE="$ROOT_DIR/.env.local"
CONFIG_PATH="$ROOT_DIR/config/config.github-ai.yaml"

if [ ! -f "$PYTHON_BIN" ]; then
  echo "未找到 Python 运行环境: $PYTHON_BIN"
  exit 1
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

cd "$ROOT_DIR"
CONFIG_PATH="$CONFIG_PATH" "$PYTHON_BIN" -m trendradar --export-feishu-base-preview
