#!/usr/bin/env bash
# Build 對話模式的 bundle（frontend-chat → frontend/chat）。
#
# frontend/chat/ 是 build 產物，不進 git（見 .gitignore）：它整包重產、壓成一行、檔名帶
# hash，放在 git 裡的話，兩條 branch 只要都 build 過，rebase / merge 就一定衝突。
# 改成在跑服務的地方現場 build。run_backend.py 啟動前會自動呼叫這支，平常不用手動跑；
# 改完前端想在不重啟 backend 的情況下看結果時，再手動跑一次即可。
#
# 用法：
#   PhotoAgent/scripts/build_chat_bundle.sh            # 只 build
#   PhotoAgent/scripts/build_chat_bundle.sh --check    # 先 tsc typecheck 再 build
set -euo pipefail

CHECK=0
case "${1:-}" in
  --check) CHECK=1 ;;
  "") ;;
  *) echo "用法：$0 [--check]" >&2; exit 2 ;;
esac

PHOTOAGENT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PHOTOAGENT/frontend-chat"

# PATH 裡沒有 node 時，用 VSCode server 底下那顆。
if ! command -v node >/dev/null 2>&1; then
  NODE_BIN="$(find ~/.vscode-server -maxdepth 6 -type f -name node 2>/dev/null | head -1 || true)"
  if [ -z "$NODE_BIN" ]; then
    echo "找不到 node：PATH 裡沒有，~/.vscode-server 底下也沒有。請先安裝 Node.js 20 以上。" >&2
    exit 1
  fi
  export PATH="$(dirname "$NODE_BIN"):$PATH"
fi

# 第一次 build 時安裝依賴。之後 package.json 有改依賴的話，要自己再跑一次 npm ci。
if [ ! -x node_modules/.bin/vite ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "frontend-chat 的依賴沒裝或已過期，但找不到 npm 可以安裝。" >&2
    echo "請在有 npm 的環境裡到 PhotoAgent/frontend-chat 跑 npm ci。" >&2
    exit 1
  fi
  npm ci --no-audit --no-fund
fi

if [ "$CHECK" -eq 1 ]; then
  ./node_modules/.bin/tsc --noEmit
fi
./node_modules/.bin/vite build --logLevel warn
echo "chat bundle 已 build 到 frontend/chat/。"
