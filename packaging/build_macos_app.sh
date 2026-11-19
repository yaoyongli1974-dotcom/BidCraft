#!/usr/bin/env bash
# macOS：构建 .app 并打成 zip（需在 macOS 上执行）。
set -euo pipefail
cd "$(dirname "$0")/.."
if ! .venv/bin/python -c "import PyInstaller" 2>/dev/null; then
  echo "未安装 PyInstaller：uv pip install pyinstaller" >&2
  exit 1
fi
VERSION="${VERSION:-$(.venv/bin/python -c 'from bidcraft import __version__; print(__version__)')}"
rm -rf build/pyinstaller dist/BidCraft.app dist/BidCraft
.venv/bin/python -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller --windowed packaging/bidcraft.spec
cd dist
ditto -c -k --sequesterRsrc --keepParent BidCraft.app "bidcraft-desktop-${VERSION}-macos.zip"
echo "产物：dist/bidcraft-desktop-${VERSION}-macos.zip"
