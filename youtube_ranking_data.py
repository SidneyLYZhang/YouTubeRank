import os
import asyncio
import random
import smtplib
import pandas as pd
import pendulum as plm
from email.message import EmailMessage
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


class DataFetchError(RuntimeError):
    """排名页面在多次重试后仍无法获取（反爬封锁/网络异常/页面结构变更）"""


def buildURL(keyName:str|None = None) -> str:
    """构造排名页 URL"""
    if keyName and keyName not in ["CN","HK","MO","TW"] :
        raise KeyError("错误输入")
    region = keyName or "global"
    return f"https://socialblade.com/youtube/lists/top/100/subscribers/all/{region}"


CRAWLER_MODES = ("stealth", "undetected", "basic")

def crawlerMode() -> str :
    """反检测层级（CRAWLER_MODE 环境变量）：

    stealth    —— 默认。playwright-stealth 指纹修补 + magic + 随机 UA（实测可破 Cloudflare 403）
    undetected —— stealth 失效时改用 patchright 适配器（对抗更高强度反爬）
    basic      —— 不做任何伪装，仅用于排查原始状态
    """
    mode = (os.environ.get("CRAWLER_MODE") or "stealth").strip().lower()
    if mode not in CRAWLER_MODES :
        raise ValueError(f"CRAWLER_MODE 只支持 {CRAWLER_MODES}，当前: {mode}")
    return mode

def buildBrowserConfig() -> BrowserConfig :
    """浏览器级配置：stealth 指纹修补、随机 UA、真实桌面视口

    crawl4ai 默认视口 1080x600 明显不合常理，也属于可被识别的特征，统一改成 1920x1080。
    CRAWLER_HEADFUL=true 可在本地以有头模式调试（CI 无显示器须保持无头）。
    """
    mode = crawlerMode()
    headful = os.environ.get("CRAWLER_HEADFUL", "").strip().lower() in ("1", "true", "yes", "on")
    return BrowserConfig(
        headless=not headful,
        enable_stealth=(mode == "stealth"),
        user_agent_mode="random",
        viewport_width=1920,
        viewport_height=1080,
    )

def buildRunConfig() -> CrawlerRunConfig :
    """页面级配置：magic 注入 navigator 覆盖并模拟人类交互（basic 模式关闭）

    delay_before_return_html 留出时间让 Cloudflare 的 JS challenge 自行完成跳转，
    否则只会抓到 "Just a moment..." 的中转页。
    """
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        magic=(crawlerMode() != "basic"),
        delay_before_return_html=10.0,
    )

def buildCrawler() -> AsyncWebCrawler :
    """按 CRAWLER_MODE 组装爬虫；undetected 模式使用 patchright 适配器"""
    browserConfig = buildBrowserConfig()
    if crawlerMode() == "undetected" :
        from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
        from crawl4ai.browser_adapter import UndetectedAdapter
        strategy = AsyncPlaywrightCrawlerStrategy(
            browser_config=browserConfig, browser_adapter=UndetectedAdapter())
        return AsyncWebCrawler(crawler_strategy=strategy, config=browserConfig)
    return AsyncWebCrawler(config=browserConfig)


def isAntiBotError(err:str) -> bool :
    """是否属于反爬拦截（Cloudflare challenge / 403 封锁页）"""
    err = (err or "").lower()
    return "anti-bot" in err or "challenge" in err or "blocked" in err


async def fetchRankingTable(crawler, url:str, *, runConfig:CrawlerRunConfig|None = None,
                            max_attempts:int = 3, base_delay:float = 30.0,
                            challenge_delay:float = 180.0):
    """抓取单个排名页并返回数据表。

    反爬封锁（如 CI 数据中心 IP 被 403）、网络异常、非 200 状态、
    页面解析不出表格，均视为可重试失败，按指数退避重试；
    重试耗尽后抛出带原因的 DataFetchError，而不是裸 IndexError。

    反爬拦截单独用 challenge_delay（默认 180s）起步：实测 Cloudflare 的
    JS challenge 需要按分钟级冷却才自行解除，30s 级别的重试基本无效。
    """
    runConfig = runConfig or buildRunConfig()
    lastErr = "未知错误"
    for attempt in range(1, max_attempts + 1):
        try:
            result = await crawler.arun(url=url, config=runConfig)
            if result.success and result.status_code == 200 and result.tables:
                return result.tables[0]
            if result.error_message :
                lastErr = result.error_message
            elif result.status_code != 200 :
                lastErr = f"HTTP {result.status_code}"
            else :
                lastErr = f"页面未解析出数据表格(HTTP 200, tables={len(result.tables)})，页面结构可能已变更"
        except Exception as exc :
            lastErr = f"{type(exc).__name__}: {exc}"
        print(f"[WARN] 第 {attempt}/{max_attempts} 次抓取失败: {url} -> {lastErr}")
        if attempt < max_attempts :
            delayBase = challenge_delay if isAntiBotError(lastErr) else base_delay
            delay = delayBase * 2 ** (attempt - 1) + random.uniform(0, delayBase * 0.5)
            print(f"[INFO] {delay:.0f}s 后重试")
            await asyncio.sleep(delay)
    raise DataFetchError(f"抓取失败({max_attempts} 次尝试耗尽): {url} -> {lastErr}")


async def fetchAllTables(max_attempts:int = 3, base_delay:float = 30.0,
                         challenge_delay:float = 180.0, page_gap:float|None = None) -> dict:
    """依次抓取全球 + 各地区排名表。

    每一页使用独立浏览器会话：实测同一会话连抓会被 Cloudflare 逐步升级风控，
    独立会话 + 页间冷却的通过率明显更高。
    """
    gap = page_gap
    if gap is None :
        gap = float(os.environ.get("PAGE_GAP_SECONDS") or 60)
    tables = {}
    runConfig = buildRunConfig()
    for i, k in enumerate(["global", "CN", "HK", "MO", "TW"]) :
        if i :
            await asyncio.sleep(gap + random.uniform(0, gap * 0.3))  # 页间冷却，降低触发风控概率
        async with buildCrawler() as crawler:
            tables[k] = await fetchRankingTable(
                crawler, buildURL(None if k == "global" else k), runConfig=runConfig,
                max_attempts=max_attempts, base_delay=base_delay, challenge_delay=challenge_delay)
    return tables

def to_pandas(dataDict):
    """转换数据"""
    return pd.DataFrame(dataDict["rows"], columns=dataDict["headers"])

def STRTONum(data:str) -> float:
    """优化数据"""
    key = data[-1]
    vaule = data[:-1]
    if vaule == '' :
        vaule = 0
    else :
        vaule = float(vaule)
    if key == "K":
        return vaule*1000
    elif key == "M" :
        return vaule*1000000
    elif key == "B" :
        return vaule*1000000000
    else :
        return float(data)

def transData(data):
    """增加数据表达"""
    res = data.copy()
    for ic in ["subscribers","views","videos"]:
        res[ic] = res[ic].apply(STRTONum)
        if ic != "videos" :
            res[f"{ic}(万)"] = res[ic]/10000
    res = res[res["views"]!=0]
    return res

def main(save_file):
    """整体数据提取"""
    tables = asyncio.run(fetchAllTables())
    globalData = to_pandas(tables["global"])
    chinaData = pd.concat([to_pandas(tables[k]) for k in ["CN","HK","MO","TW"]])
    globalData = transData(globalData)
    chinaData = transData(chinaData)
    globalData = globalData.sort_values(by="subscribers",ascending=False)
    chinaData = chinaData.sort_values(by="subscribers",ascending=False)
    with pd.ExcelWriter(save_file) as writer:
        globalData.to_excel(writer, sheet_name="全球数据排行", index=False)
        chinaData.to_excel(writer, sheet_name="中国数据排行", index=False)

def getENV(key:str) -> str :
    return os.environ.get(key)

def isCI() -> bool :
    """是否运行在 GitHub Actions 环境（Actions 会自动注入 GITHUB_ACTIONS=true）"""
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"

def shouldSendMail() -> bool :
    """是否发送邮件。

    SEND_MAIL 环境变量显式配置优先（true/false，本地想试发邮件就设 true）；
    未配置时按运行环境自动判断：仅在 CI（GitHub Actions）发送，本地静默。
    """
    configured = os.environ.get("SEND_MAIL")
    if configured is not None :
        return configured.strip().lower() in ("1", "true", "yes", "on")
    return isCI()

SMTP_SERVERS = {
    # 发件邮箱域名 -> (SMTP 服务器, 端口)；465 走 SSL，587 走 STARTTLS
    "qq.com": ("smtp.qq.com", 465),
    "foxmail.com": ("smtp.qq.com", 465),
    "163.com": ("smtp.163.com", 465),
    "126.com": ("smtp.126.com", 465),
    "gmail.com": ("smtp.gmail.com", 465),
    "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
}

def smtpServer(mailAddr:str) -> tuple[str,int] :
    """按发件邮箱域名推断 SMTP 服务器；SMTP_HOST/SMTP_PORT 环境变量可强制覆盖"""
    host = getENV("SMTP_HOST")
    if host :
        return host, int(getENV("SMTP_PORT") or 465)
    domain = mailAddr.rsplit("@", 1)[-1].lower()
    if domain not in SMTP_SERVERS :
        raise ValueError(f"未知发件邮箱域名 {domain}，请设置 SMTP_HOST/SMTP_PORT 环境变量")
    return SMTP_SERVERS[domain]

def buildMailMessage(filename:str, themonth:str) -> EmailMessage :
    """构建带 Excel 附件的邮件（不发送，便于独立测试）"""
    msg = EmailMessage()
    msg["Subject"] = f"YouTube总排名{themonth}月度"
    msg["From"] = getENV("WORK_MAIL")
    msg["To"] = getENV("CC_MAIL")
    msg.set_content(f"这是 {themonth} 的YouTube总排名数据。")
    with open(filename, "rb") as f :
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=os.path.basename(filename),
        )
    return msg

def sendReport(filename:str, themonth:str) -> None :
    """把生成的排名表作为附件发送（Python 标准库 smtplib 实现）"""
    myMail = getENV("WORK_MAIL")
    password = getENV("MAIL_PASSWORDS")
    msg = buildMailMessage(filename, themonth)
    host, port = smtpServer(myMail)
    if port == 587 :
        with smtplib.SMTP(host, port, timeout=60) as server :
            server.starttls()
            server.login(myMail, password)
            server.send_message(msg)
    else :
        with smtplib.SMTP_SSL(host, port, timeout=60) as server :
            server.login(myMail, password)
            server.send_message(msg)


if __name__ == "__main__":
    # 提取数据
    themonth = plm.now().subtract(months=1).format("YYYYMM")
    filename = f"月度YouTube排名数据_{themonth}.xlsx"
    main(filename)
    # 发送数据：仅 CI 环境发送，本地只落盘
    if shouldSendMail() :
        sendReport(filename, themonth)
    else :
        print(f"[INFO] 本地环境，跳过邮件发送，数据已保存: {filename}")
