"""
本地调试入口
生产定时任务由 systemd user timer 调度（见 deploy/systemd/*），不再用 Prefect。
"""
import argparse
import sys

from flows.sync_inventory import sync_inventory_flow
from core.db import init_db
from core.config import settings


def run_direct():
    """直接运行（开发调试用）"""
    init_db()
    result = sync_inventory_flow()
    return result


def start_web(host: str | None = None, port: int | None = None, reload: bool = True):
    """启动 Web 服务（默认仅监听 127.0.0.1，见 APIConfig）"""
    import uvicorn
    uvicorn.run(
        "web.app:app",
        host=host or settings.api.host,
        port=port or settings.api.port,
        reload=reload,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="跨境电商运营数据中台")
    parser.add_argument(
        "--task",
        choices=["inventory", "web", "alert-scan"],
        default="inventory",
        help="指定要运行的任务"
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Web 服务监听地址 (默认取 API__HOST, 即 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Web 服务端口 (默认取 API__PORT, 即 8000)"
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="禁用热重载"
    )
    args = parser.parse_args()

    if args.task == "web":
        start_web(host=args.host, port=args.port, reload=not args.no_reload)
    elif args.task == "alert-scan":
        from flows.scan_fulfillment_alerts import scan_fulfillment_alerts_flow
        scan_fulfillment_alerts_flow()
    else:
        run_direct()
