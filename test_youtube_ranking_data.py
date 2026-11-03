"""回归测试：SocialBlade 反爬封锁场景下的抓取韧性。

背景：CI（GitHub Actions 数据中心 IP）间歇性被 SocialBlade 反爬 403 封锁，
旧代码 `result.tables[0]` 在封锁页上抛裸 IndexError，且无重试。
本文件锁定修复后的行为：重试 + 带原因的明确报错。
"""
import asyncio

import pytest

from youtube_ranking_data import (
    DataFetchError,
    buildBrowserConfig,
    buildMailMessage,
    buildRunConfig,
    buildURL,
    crawlerMode,
    fetchRankingTable,
    isAntiBotError,
    shouldSendMail,
    smtpServer,
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
    kwargs.setdefault("challenge_delay", 0.001)
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


def test_send_mail_in_ci_by_default(monkeypatch):
    """CI 环境默认发邮件，本地环境默认不发"""
    monkeypatch.delenv("SEND_MAIL", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert shouldSendMail() is True
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    assert shouldSendMail() is False
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert shouldSendMail() is False


def test_send_mail_explicit_config_overrides_environment(monkeypatch):
    """SEND_MAIL 显式配置优先于环境自动判断"""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("SEND_MAIL", "false")
    assert shouldSendMail() is False
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    monkeypatch.setenv("SEND_MAIL", "true")
    assert shouldSendMail() is True
    monkeypatch.setenv("SEND_MAIL", "0")
    assert shouldSendMail() is False


def test_smtp_server_inference_and_override(monkeypatch):
    """按发件邮箱域名推断 SMTP；SMTP_HOST/SMTP_PORT 可强制覆盖"""
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    assert smtpServer("sender@qq.com") == ("smtp.qq.com", 465)
    assert smtpServer("someone@OutLook.COM") == ("smtp-mail.outlook.com", 587)
    with pytest.raises(ValueError):
        smtpServer("someone@unknown.example")
    monkeypatch.setenv("SMTP_HOST", "smtp.corp.cn")
    monkeypatch.setenv("SMTP_PORT", "587")
    assert smtpServer("sender@unknown.example") == ("smtp.corp.cn", 587)


def test_build_mail_message_layout(tmp_path, monkeypatch):
    """邮件构建：主题/发件人/收件人/正文/Excel 附件齐全"""
    monkeypatch.setenv("WORK_MAIL", "sender@qq.com")
    monkeypatch.setenv("CC_MAIL", "boss@example.com")
    xlsx = tmp_path / "月度YouTube排名数据_202610.xlsx"
    xlsx.write_bytes(b"fake-excel-bytes")

    msg = buildMailMessage(str(xlsx), "202610")

    assert msg["Subject"] == "YouTube总排名202610月度"
    assert msg["From"] == "sender@qq.com"
    assert msg["To"] == "boss@example.com"
    assert "202610" in msg.get_body(preferencelist=("plain",)).get_content()
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "月度YouTube排名数据_202610.xlsx"
    assert attachments[0].get_content_type() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_crawler_defaults_to_stealth(monkeypatch):
    """默认走 stealth 层：指纹修补 + 随机 UA + 真实视口 + magic"""
    monkeypatch.delenv("CRAWLER_MODE", raising=False)
    monkeypatch.delenv("CRAWLER_HEADFUL", raising=False)
    assert crawlerMode() == "stealth"

    browser = buildBrowserConfig()
    assert browser.enable_stealth is True
    assert browser.user_agent_mode == "random"
    assert browser.headless is True
    assert (browser.viewport_width, browser.viewport_height) == (1920, 1080)

    assert buildRunConfig().magic is True


def test_crawler_mode_switches_layers(monkeypatch):
    """basic 关闭所有伪装；undetected 关闭 stealth（两者不可叠加）"""
    monkeypatch.delenv("CRAWLER_HEADFUL", raising=False)

    monkeypatch.setenv("CRAWLER_MODE", "basic")
    browser = buildBrowserConfig()
    assert browser.enable_stealth is False
    assert buildRunConfig().magic is False

    monkeypatch.setenv("CRAWLER_MODE", "undetected")
    assert buildBrowserConfig().enable_stealth is False
    assert buildRunConfig().magic is True

    monkeypatch.setenv("CRAWLER_MODE", "Stealth")
    assert crawlerMode() == "stealth"

    monkeypatch.setenv("CRAWLER_MODE", "unknown")
    with pytest.raises(ValueError):
        crawlerMode()


def test_crawler_headful_override(monkeypatch):
    """CRAWLER_HEADFUL 控制有头调试模式"""
    monkeypatch.delenv("CRAWLER_MODE", raising=False)
    monkeypatch.setenv("CRAWLER_HEADFUL", "true")
    assert buildBrowserConfig().headless is False
    monkeypatch.setenv("CRAWLER_HEADFUL", "0")
    assert buildBrowserConfig().headless is True


def test_antibot_error_classification():
    """反爬拦截要与网络异常区分开，前者用更长的冷却"""
    assert isAntiBotError("Blocked by anti-bot protection: Cloudflare JS challenge") is True
    assert isAntiBotError("Blocked by anti-bot protection: HTTP 403 with HTML content") is True
    assert isAntiBotError("页面未解析出数据表格(HTTP 200, tables=0)") is False
    assert isAntiBotError("ConnectionError: Request timed out") is False
    assert isAntiBotError("") is False
