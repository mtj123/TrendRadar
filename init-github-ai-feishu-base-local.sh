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

if [ ! -f "$ENV_FILE" ]; then
  echo "未找到 $ENV_FILE"
  echo "先复制 .env.local.example 为 .env.local 并填入 FEISHU_BASE_*"
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

has_placeholder() {
  local value="${1:-}"
  local normalized
  normalized="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  if [ -z "$value" ] || [ "${#value}" -lt 8 ]; then
    return 0
  fi
  [[ "$normalized" == *"xxx"* || "$normalized" == *"example"* || "$normalized" == *"replace_me"* || "$normalized" == *"your_"* ]]
}

if [ -z "${FEISHU_BASE_APP_ID:-}" ] || [ -z "${FEISHU_BASE_APP_SECRET:-}" ] || [ -z "${FEISHU_BASE_APP_TOKEN:-}" ]; then
  echo "FEISHU_BASE_* 配置不完整，请先在 .env.local 中填写"
  exit 1
fi

if has_placeholder "${FEISHU_BASE_APP_ID:-}" || has_placeholder "${FEISHU_BASE_APP_SECRET:-}" || has_placeholder "${FEISHU_BASE_APP_TOKEN:-}"; then
  echo "检测到 FEISHU_BASE_* 仍是示例占位值，请先替换为真实飞书凭证"
  exit 1
fi

cd "$ROOT_DIR"
CONFIG_PATH="$CONFIG_PATH" "$PYTHON_BIN" -m trendradar --feishu-base-init
