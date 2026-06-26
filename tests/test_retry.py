"""core.retry 装饰器测试：对齐 Prefect `@task(retries=, retry_delay_seconds=)` 语义。

retries=N = 首次失败后再重试 N 次（共最多 N+1 次尝试）；最后一次仍失败抛原异常；
成功即返回不再重试；重试前 sleep(delay_seconds)（这里 mock 掉 time.sleep 不真等）。
"""
from __future__ import annotations

import pytest

from core.retry import retry


def test_success_first_try_no_retry():
    calls = []

    @retry(retries=3, delay_seconds=60)
    def ok():
        calls.append(1)
        return "done"

    assert ok() == "done"
    assert len(calls) == 1  # 成功即停，不重试


def test_retries_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr("core.retry.time.sleep", lambda s: slept.append(s))
    attempts = []

    @retry(retries=3, delay_seconds=30)
    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ValueError("boom")
        return "ok"

    assert flaky() == "ok"
    assert len(attempts) == 3       # 失败2次后第3次成功
    assert slept == [30, 30]        # 两次重试各 sleep 一次


def test_exhausts_and_raises_original(monkeypatch):
    monkeypatch.setattr("core.retry.time.sleep", lambda s: None)
    attempts = []

    @retry(retries=2, delay_seconds=10)
    def always_fail():
        attempts.append(1)
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError, match="nope"):
        always_fail()
    assert len(attempts) == 3       # 1 次首尝试 + 2 次重试 = 3


def test_zero_retries_calls_once(monkeypatch):
    monkeypatch.setattr("core.retry.time.sleep", lambda s: None)
    attempts = []

    @retry()  # retries=0
    def once():
        attempts.append(1)
        raise ValueError("x")

    with pytest.raises(ValueError):
        once()
    assert len(attempts) == 1       # 不重试


def test_preserves_args_and_name():
    @retry(retries=1)
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
    assert add.__name__ == "add"    # functools.wraps 保留元数据
