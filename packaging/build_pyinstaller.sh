#!/usr/bin/env bash
# 用 PyInstaller 构建目录形态的桌面客户端（Linux/Windows/macOS 通用）。
set -euo pipefail
cd "$(dirname "$0")/.."
if ! .venv/bin/python -c "import PyInstaller" 2>/dev/null; then
  echo "未安装 PyInstaller：uv pip install pyinstaller" >&2
  exit 1
fi
rm -rf build/pyinstaller dist/BidCraft
.venv/bin/python -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller packaging/bidcraft.spec
echo "产物：dist/BidCraft/"
