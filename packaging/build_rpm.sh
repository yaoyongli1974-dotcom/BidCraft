#!/usr/bin/env bash
# 构建 .rpm（通过 nfpm；也可在 CI 的 fedora 容器中用 rpmbuild）。
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${VERSION:-$(.venv/bin/python -c 'from bidcraft import __version__; print(__version__)')}"
ARCH="${ARCH:-$(uname -m)}"
[ -d dist/BidCraft ] || packaging/build_pyinstaller.sh
if ! command -v nfpm >/dev/null 2>&1; then
  echo "未安装 nfpm：https://nfpm.goreleaser.com/install/ （或使用 CI workflow）" >&2
  exit 1
fi
VERSION="$VERSION" ARCH="$ARCH" nfpm package -f packaging/nfpm.yaml -p rpm -t dist/
echo "产物：dist/*.rpm"
