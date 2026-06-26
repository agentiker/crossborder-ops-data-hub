"""轻量重试装饰器——零依赖，替代 Prefect `@task(retries=, retry_delay_seconds=)`。

背景：调度由 systemd user timer 承担，Prefect 在本项目里**唯一**真正用到的能力就是
`@task` 的失败重试，却为此每次跑 flow 都拉起重型运行时 + 临时 server（单跑 ~200–300 MB，
journald 里的 `Stopping temporary server on 127.0.0.1:80xx`）。剥掉它换成本装饰器，
timer 单跑内存从 ~250 MB 降到 ~50–100 MB（见 docs/production-deployment.md §1.1）。

语义与 Prefect 对齐：`@retry(retries=N, delay_seconds=M)` = 首次失败后**再重试 N 次**、
每次间隔 M 秒（共最多 N+1 次尝试）。retries=0 即不重试。最后一次仍失败抛原异常
（→ flow 函数抛出 → 进程非零退出 → systemd OnFailure 飞书告警，与原行为一致）。
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)


def retry(
    retries: int = 0,
    delay_seconds: float = 0,
    *,
    name: str | None = None,
) -> Callable[[F], F]:
    """失败重试装饰器。

    retries: 首次失败后额外重试的次数（同 Prefect `retries`）。
    delay_seconds: 每次重试前的等待秒数（同 Prefect `retry_delay_seconds`）。
    name: 仅用于日志标识，默认取被装饰函数名。
    """

    def deco(fn: F) -> F:
        label = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 —— 与 Prefect 一致，任何异常都重试
                    if attempt >= retries:
                        raise
                    attempt += 1
                    logger.warning(
                        "[retry] %s 第 %d/%d 次失败：%s；%.0fs 后重试",
                        label, attempt, retries, exc, delay_seconds,
                    )
                    if delay_seconds:
                        time.sleep(delay_seconds)

        return wrapper  # type: ignore[return-value]

    return deco
