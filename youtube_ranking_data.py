import os
import asyncio
import random
import pandas as pd
import pendulum as plm
from pytoolsz_lnx.utils import quicksendmail
from crawl4ai import AsyncWebCrawler, CacheMode


class DataFetchError(RuntimeError):
    """排名页面在多次重试后仍无法获取（反爬封锁/网络异常/页面结构变更）"""


def buildURL(keyName:str|None = None) -> str:
    """构造排名页 URL"""
    if keyName and keyName not in ["CN","HK","MO","TW"] :
        raise KeyError("错误输入")
    region = keyName or "global"
    return f"https://socialblade.com/youtube/lists/top/100/subscribers/all/{region}"


async def fetchRankingTable(crawler, url:str, *, max_attempts:int = 4, base_delay:float = 30.0):
    """抓取单个排名页并返回数据表。

    反爬封锁（如 CI 数据中心 IP 被 403）、网络异常、非 200 状态、
    页面解析不出表格，均视为可重试失败，按指数退避重试；
    重试耗尽后抛出带原因的 DataFetchError，而不是裸 IndexError。
    """
    lastErr = "未知错误"
    for attempt in range(1, max_attempts + 1):
        try:
            result = await crawler.arun(url=url, cache_mode=CacheMode.BYPASS)
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
            delay = base_delay * 2 ** (attempt - 1) + random.uniform(0, base_delay * 0.5)
            await asyncio.sleep(delay)
    raise DataFetchError(f"抓取失败({max_attempts} 次尝试耗尽): {url} -> {lastErr}")


async def fetchAllTables(max_attempts:int = 4, base_delay:float = 30.0) -> dict:
    """单个浏览器会话内抓取全球 + 各地区排名表"""
    tables = {}
    async with AsyncWebCrawler() as crawler:
        tables["global"] = await fetchRankingTable(
            crawler, buildURL(), max_attempts=max_attempts, base_delay=base_delay)
        for k in ["CN","HK","MO","TW"] :
            await asyncio.sleep(round(random.random()*10, 2))  # 随机间隔，降低触发限流概率
            tables[k] = await fetchRankingTable(
                crawler, buildURL(k), max_attempts=max_attempts, base_delay=base_delay)
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


if __name__ == "__main__":
    # 提取数据
    themonth = plm.now().subtract(months=1).format("YYYYMM")
    filename = f"月度YouTube排名数据_{themonth}.xlsx"
    main(filename)
    # 发送数据
    quicksendmail(
        myMail=getENV("WORK_MAIL"),
        password=getENV("MAIL_PASSWORDS"),
        mailText=f"这是 {themonth} 的YouTube总排名数据。",
        attachments=[filename],
        subject=f"YouTube总排名{themonth}月度",
        recipients=[getENV("CC_MAIL")],
        other_smtp_config={"qq.com":("smtp.qq.com",465)}
    )
