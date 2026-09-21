#!/usr/bin/env bash
# web-solo 依赖自检与安装。幂等：依赖齐备时秒退，缺什么装什么。
# 只往当前 python3 里装 playwright 包和 chromium，不动 shell 配置、不建 venv。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${WEB_SOLO_PYTHON:-python3}"
LOG="$(mktemp -t web-solo-setup)"
trap 'rm -f "$LOG"' EXIT

say() { printf '%s\n' "$*"; }

# ---- 加速层：OpenCLI（跟 web-solo 一起装，装不上也不阻断本脚本）-------------
# 装进仓库自己的 node_modules，锁版本，不动全局 npm 环境。见 references/opencli.md。
OPENCLI_PKG="@jackwener/opencli@1.7.22"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_OPENCLI="$REPO_ROOT/node_modules/.bin/opencli"

# 可移植超时：有 perl 用 perl alarm，没有就直接跑（宁可慢也不假死在这）。
with_timeout() {
  local secs="$1"; shift
  if command -v perl >/dev/null 2>&1; then
    perl -e 'alarm shift; exec @ARGV' "$secs" "$@"
  else
    "$@"
  fi
}

ensure_opencli() {
  if [ -x "$LOCAL_OPENCLI" ]; then
    say "✅ 加速层 OpenCLI 已就位：$LOCAL_OPENCLI"
    return 0
  fi
  if command -v opencli >/dev/null 2>&1; then
    say "✅ 加速层 OpenCLI 已就位：$(command -v opencli)（全局）"
    return 0
  fi
  if ! command -v npm >/dev/null 2>&1; then
    say "○ 加速层 OpenCLI 跳过：这台机器没有 npm。browser.py 不依赖它，功能完整。"
    say "   想要它：装 Node.js 后重跑本脚本。"
    return 0
  fi
  say "→ 安装加速层 OpenCLI（${OPENCLI_PKG}，装进本目录 node_modules，不动全局）……"
  if with_timeout 180 npm i --prefix "$REPO_ROOT" "$OPENCLI_PKG" >"$LOG" 2>&1 && [ -x "$LOCAL_OPENCLI" ]; then
    say "✅ 加速层 OpenCLI 安装完成：$LOCAL_OPENCLI"
  else
    say "○ 加速层 OpenCLI 没装上（网络或 npm 权限），**不影响使用**——browser.py 不依赖它。"
    say "   想重试：npm i --prefix \"$REPO_ROOT\" $OPENCLI_PKG"
  fi
  return 0
}

# ---- 快路径：全齐就走人 -----------------------------------------------------
if command -v "$PY" >/dev/null 2>&1 && "$PY" "$SCRIPT_DIR/browser.py" doctor >/dev/null 2>&1; then
  say "web-solo: 依赖齐备（python3 + playwright + chromium），无需安装。"
  ensure_opencli
  exit 0
fi

say "web-solo: 开始依赖自检……"

# ---- 1. python3 -------------------------------------------------------------
if ! command -v "$PY" >/dev/null 2>&1; then
  say "❌ 找不到 python3。"
  say "   macOS:  brew install python"
  say "   其它:   https://www.python.org/downloads/"
  say "   已装但不叫 python3：export WEB_SOLO_PYTHON=/path/to/python 后重跑本脚本。"
  exit 1
fi
say "✅ python3: $("$PY" -V 2>&1) ($("$PY" -c 'import sys; print(sys.executable)'))"

# ---- 2. playwright 包 -------------------------------------------------------
pip_install() {
  local pkg="$1"
  if "$PY" -m pip install "$pkg" >"$LOG" 2>&1; then return 0; fi
  if grep -qi "externally-managed-environment" "$LOG"; then
    say "   系统 python 受保护，改用 --user 重试……"
  else
    say "   直接安装失败，改用 --user 重试……"
  fi
  if "$PY" -m pip install --user "$pkg" >"$LOG" 2>&1; then return 0; fi
  return 1
}

if "$PY" -c 'import playwright' >/dev/null 2>&1; then
  say "✅ playwright 包已安装：$("$PY" -c 'import importlib.metadata as m; print(m.version("playwright"))')"
else
  if ! "$PY" -m pip --version >/dev/null 2>&1; then
    say "❌ 这个 python 没有可用的 pip。"
    say "   先跑：$PY -m ensurepip --upgrade"
    say "   再重跑本脚本。"
    exit 1
  fi
  say "→ 安装 playwright 包（几 MB，十几秒）……"
  if pip_install playwright; then
    say "✅ playwright 包安装完成。"
  else
    say "❌ playwright 包安装失败，末尾日志："
    tail -n 15 "$LOG" | sed 's/^/   /'
    if grep -qiE "network|timed out|temporary failure|could not fetch|connection" "$LOG"; then
      say "   看起来是网络问题：换网络或配 pip 镜像后重跑本脚本。"
    elif grep -qi "externally-managed-environment" "$LOG"; then
      say "   系统 python 被发行版锁定。建议自建虚拟环境后重跑："
      say "     $PY -m venv ~/.web-solo-venv && ~/.web-solo-venv/bin/pip install playwright"
      say "     export WEB_SOLO_PYTHON=~/.web-solo-venv/bin/python"
    else
      say "   手动安装：$PY -m pip install --user playwright"
    fi
    exit 1
  fi
fi

# ---- 3. chromium 浏览器 -----------------------------------------------------
chromium_ready() {
  "$PY" - <<'PYCHK' >/dev/null 2>&1
import os, sys
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    sys.exit(0 if os.path.exists(pw.chromium.executable_path) else 1)
PYCHK
}

if chromium_ready; then
  say "✅ chromium 已下载。"
else
  say "→ 下载 Playwright 自带的 chromium：约 95 MB（解压后占约 430 MB），首次 1–5 分钟（看网速），下面是官方下载进度，别中断。"
  if "$PY" -m playwright install chromium; then
    say "✅ chromium 下载完成。"
  else
    say "❌ chromium 下载失败。常见原因是网络受限。"
    say "   重试：$PY -m playwright install chromium"
    say "   国内网络可先设镜像：export PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright"
    exit 1
  fi
fi

# ---- 4. 复核 ---------------------------------------------------------------
if "$PY" "$SCRIPT_DIR/browser.py" doctor; then
  say "web-solo: 依赖就绪。"
  ensure_opencli
  exit 0
fi
say "❌ 自检未通过，见上面的 doctor 输出。"
exit 1
