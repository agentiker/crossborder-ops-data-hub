"""
本地调试入口
生产部署请使用 prefect deploy --all（配置见 prefect.yaml）
"""
import argparse
from flows.sync_inventory import sync_inventory_flow
from core.db import init_db


def run_direct():
    """直接运行（开发调试用）"""
    init_db()
    result = sync_inventory_flow()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="跨境电商运营数据中台")
    parser.add_argument(
        "--task",
        choices=["inventory"],
        default="inventory",
        help="指定要运行的任务"
    )
    args = parser.parse_args()

    run_direct()
