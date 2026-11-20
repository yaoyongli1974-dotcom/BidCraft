#!/usr/bin/env bash
# 用 PyInstaller 构建目录形态的桌面客户端（Linux/Windows/macOS 通用）。
set -euo pipefail
cd "$(dirname "$0")/.."
# 桌面客户端已归档（v0.6.3）：依赖固化在 pyproject 的 desktop/build extra，
# 精确版本由 uv.lock 锁定，避免「构建机上碰巧装了什么」决定产物。
missing=$(.venv/bin/python - <<'PY'
import importlib.util
mods = {"PyInstaller": "pyinstaller", "qtpy": "qtpy", "webview": "pywebview", "PySide6": "PySide6"}
print(" ".join(pkg for mod, pkg in mods.items() if importlib.util.find_spec(mod) is None))
PY
)
if [ -n "$missing" ]; then
  echo "缺少桌面构建依赖：$missing" >&2
  echo "请执行：uv sync --frozen --extra desktop --extra build" >&2
  exit 1
fi
rm -rf build/pyinstaller dist/BidCraft
.venv/bin/python -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller packaging/bidcraft.spec
echo "产物：dist/BidCraft/"
