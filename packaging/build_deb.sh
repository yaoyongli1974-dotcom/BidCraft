#!/usr/bin/env bash
# 构建 .deb（仅需 dpkg-deb，无需 debhelper）。
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${VERSION:-$(.venv/bin/python -c 'from bidcraft import __version__; print(__version__)')}"
ARCH="${ARCH:-$(dpkg --print-architecture)}"
[ -d dist/BidCraft ] || packaging/build_pyinstaller.sh

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/DEBIAN" "$ROOT/opt" "$ROOT/usr/bin" \
  "$ROOT/usr/share/applications" "$ROOT/usr/share/icons/hicolor/scalable/apps"
cp -a dist/BidCraft "$ROOT/opt/bidcraft"
cp packaging/bidcraft-desktop.sh "$ROOT/usr/bin/bidcraft-desktop"
chmod 0755 "$ROOT/usr/bin/bidcraft-desktop"
cp packaging/bidcraft.desktop "$ROOT/usr/share/applications/bidcraft.desktop"
cp packaging/bidcraft.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/bidcraft.svg"

cat > "$ROOT/DEBIAN/control" <<EOF
Package: bidcraft-desktop
Version: ${VERSION}
Section: office
Priority: optional
Architecture: ${ARCH}
Maintainer: BidCraft <noreply@bidcraft.local>
Installed-Size: $(du -sk "$ROOT/opt/bidcraft" | cut -f1)
Description: BidCraft desktop client (C/S mode)
 Local gateway that proxies a BidCraft server and exposes a local file bridge.
EOF

OUT="dist/bidcraft-desktop_${VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$ROOT" "$OUT"
echo "产物：$OUT"
