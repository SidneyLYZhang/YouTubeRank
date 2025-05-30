import os
import asyncio
from crawl4ai import AsyncWebCrawler
import pandas as pd
import time
import random
import pendulum as plm
from pytoolsz_lnx.utils import quicksendmail


async def getData(keyName:str|None = None):
    """获取数据"""
    if keyName and keyName not in ["CN","HK","MO","TW"] :
        raise KeyError("错误输入")
    if keyName :
        oURL = f"https://socialblade.com/youtube/lists/top/100/subscribers/all/{keyName}"
    else :
        oURL = "https://socialblade.com/youtube/lists/top/100/subscribers/all/global"
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url=oURL,
        )
        return result.tables[0]

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
    globalData = asyncio.run(getData())
    globalData = to_pandas(globalData)
    chinaData = pd.DataFrame()
    for k in ["CN","HK","MO","TW"] :
        time.sleep(round(random.random()*10,2))
        tmpData = asyncio.run(getData(k))
        tmpData = to_pandas(tmpData)
        chinaData = pd.concat([chinaData, tmpData])
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
        getENV("WORK_MAIL"),getENV("MAIL_PASSWORDS"),
        f"这是 {themonth} 的YouTube总排名数据。",
        [filename],
        f"YouTube总排名{themonth}月度",getENV("CC_MAIL")
    )