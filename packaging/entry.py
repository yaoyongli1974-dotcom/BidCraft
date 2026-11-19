"""打包入口：双击启动桌面客户端。

用法：BidCraft [--server URL] [--port N] [--no-browser]
服务器地址优先级：--server > 环境变量 BIDCRAFT_SERVER > 已保存的客户端配置 > 交互输入。
"""

from __future__ import annotations

import argparse

from bidcraft.client import load_config
from bidcraft.desktop import run_desktop


def main() -> None:
    parser = argparse.ArgumentParser(prog="BidCraft", description="BidCraft 桌面客户端")
    parser.add_argument("server", nargs="?", default="", help="BidCraft 服务器地址")
    parser.add_argument("--server", dest="server_flag", default="", help="BidCraft 服务器地址")
    parser.add_argument("--port", type=int, default=0, help="本地端口（默认随机）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    server = (args.server_flag or args.server).strip() or load_config().server
    if not server:
        try:
            server = input("请输入 BidCraft 服务器地址（如 https://bid.example.com）：").strip()
        except EOFError:
            server = ""
    if not server:
        print("未提供服务器地址，已退出。")
        raise SystemExit(2)
    run_desktop(server, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
