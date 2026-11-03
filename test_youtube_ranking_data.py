"""回归测试：SocialBlade 反爬封锁场景下的抓取韧性。

背景：CI（GitHub Actions 数据中心 IP）间歇性被 SocialBlade 反爬 403 封锁，
旧代码 `result.tables[0]` 在封锁页上抛裸 IndexError，且无重试。
本文件锁定修复后的行为：重试 + 带原因的明确报错。
"""
import asyncio

import pytest

from youtube_ranking_data import (
    DataFetchError,
    buildURL,
    fetchRankingTable,
)


GOOD_TABLE = {"headers": ["rank", "name"], "rows": [[1, "T-Series"]]}


class FakeResult:
    def __init__(self, success=True, status_code=200, tables=(), error_message=""):
        self.success = success
        self.status_code = status_code
        self.tables = tables
        self.error_message = error_message


def blockedResult():
    """模拟 CI 日志中的封锁结果：403 封锁页，解析不出任何表格"""
    return FakeResult(
        success=False,
        status_code=403,
        tables=[],
        error_message="Blocked by anti-bot protection: HTTP 403 with HTML content (5992 bytes)",
    )


class FakeCrawler:
    """按预定脚本逐次返回结果/抛异常的假爬虫"""
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def arun(self, url, **kwargs):
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def runFetch(crawler, **kwargs):
    kwargs.setdefault("base_delay", 0.001)
    return asyncio.run(fetchRankingTable(crawler, buildURL(), **kwargs))


def test_blocked_once_then_success_recovers():
    """第一次被封锁、重试后恢复 → 返回数据表（瞬时封锁自愈）"""
    crawler = FakeCrawler([blockedResult(), FakeResult(tables=[GOOD_TABLE])])
    table = runFetch(crawler, max_attempts=3)
    assert table == GOOD_TABLE
    assert crawler.calls == 2


def test_persistently_blocked_raises_clear_error():
    """持续被封锁 → 抛带封锁原因的 DataFetchError，而不是裸 IndexError（CI 原始故障场景）"""
    crawler = FakeCrawler([blockedResult()])
    with pytest.raises(DataFetchError) as excinfo:
        runFetch(crawler, max_attempts=2)
    assert "Blocked by anti-bot" in str(excinfo.value)
    assert buildURL() in str(excinfo.value)
    assert crawler.calls == 2


def test_empty_tables_on_http_200_reports_structure_change():
    """HTTP 200 但解析不出表格 → 明确提示页面结构可能变更"""
    crawler = FakeCrawler([FakeResult(tables=[])])
    with pytest.raises(DataFetchError) as excinfo:
        runFetch(crawler, max_attempts=1)
    assert "页面结构" in str(excinfo.value)


def test_network_exception_is_retryable():
    """网络异常（超时/连接失败）视为可重试 → 恢复后返回数据表"""
    crawler = FakeCrawler([ConnectionError("Request timed out"), FakeResult(tables=[GOOD_TABLE])])
    table = runFetch(crawler, max_attempts=3)
    assert table == GOOD_TABLE
    assert crawler.calls == 2


def test_http_500_reports_status():
    """非 200 状态码 → 报错信息中带上状态码"""
    crawler = FakeCrawler([FakeResult(success=True, status_code=500, tables=[])])
    with pytest.raises(DataFetchError) as excinfo:
        runFetch(crawler, max_attempts=1)
    assert "HTTP 500" in str(excinfo.value)


def test_build_url_rejects_unknown_region():
    with pytest.raises(KeyError):
        buildURL("US")
