"""
本地调试入口
生产部署请使用 prefect deploy --all（配置见 prefect.yaml）
"""
import argparse
import sys

from flows.sync_inventory import sync_inventory_flow
from core.db import init_db


def run_direct():
    """直接运行（开发调试用）"""
    init_db()
    result = sync_inventory_flow()
    return result


def start_web(host: str = "0.0.0.0", port: int = 8000, reload: bool = True):
    """启动 Web 服务"""
    import uvicorn
    uvicorn.run("web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="跨境电商运营数据中台")
    parser.add_argument(
        "--task",
        choices=["inventory", "web"],
        default="inventory",
        help="指定要运行的任务"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Web 服务监听地址 (默认: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Web 服务端口 (默认: 8000)"
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="禁用热重载"
    )
    args = parser.parse_args()

    if args.task == "web":
        start_web(host=args.host, port=args.port, reload=not args.no_reload)
    else:
        run_direct()
