#!/usr/bin/env bash
# 启动已安装的桌面客户端（deb/rpm 安装到 /opt/bidcraft）。
set -euo pipefail
exec /opt/bidcraft/BidCraft "$@"
