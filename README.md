# README

这就是一个用来每月记录YouTube频道排名的小工具。
这功能不是很入流就是了。

具体有什么用，其实就是给有哪些需求的Boss们看一下啊，真的是个没啥用的东西。

## 功能介绍

这玩意儿能干啥呢？就是每个月自动爬取一下YouTube上订阅量前100的频道数据，然后整理成Excel表格发邮件给你。全球的和中国地区的都有，分开两个sheet。

数据包括频道名称、订阅数、观看次数和视频数量，还贴心地把订阅数和观看次数转换成了"万"为单位，方便Boss们看。

## 安装方法

项目全面使用 [uv](https://docs.astral.sh/uv/) 管理（Python 3.14、依赖版本锁定在 `uv.lock`）。

如果真的要用这个没啥用的东西，装好 uv 后在项目根目录执行：

```bash
uv sync
```

首次运行会自动下载 Python 3.14 并创建 `.venv`，然后把依赖一次性装齐。

装完依赖后，还得初始化一下爬虫（下载浏览器内核）：

```bash
uv run crawl4ai-setup
```

## 使用方法

直接运行主脚本就行了：

```bash
uv run python youtube_ranking_data.py
```

它会自动生成一个名为"月度YouTube排名数据_YYYYMM.xlsx"的文件。

**本地和 CI 环境自动区分**：脚本通过 `GITHUB_ACTIONS` 环境变量自动识别运行环境——

- 本地运行：只生成 Excel，**不发邮件**
- GitHub Actions 运行：生成 Excel 并**自动发邮件**

想本地也发一封试试？加个环境变量就行：

```bash
SEND_MAIL=true uv run python youtube_ranking_data.py
```

反之，在 CI 里不想发邮件就设 `SEND_MAIL=false`。

## 环境变量

| 变量 | 作用 | 默认行为 |
|---|---|---|
| SEND_MAIL | 是否发邮件，显式配置优先 | 未配置时：CI 发、本地不发 |
| WORK_MAIL | 发件邮箱地址 | 仅发邮件时需要 |
| MAIL_PASSWORDS | 发件邮箱密码 | 仅发邮件时需要 |
| CC_MAIL | 收件人邮箱 | 仅发邮件时需要 |
| SMTP_HOST / SMTP_PORT | SMTP 服务器覆盖（可选） | 按发件邮箱域名自动推断 |
| CRAWLER_MODE | 反检测层级：stealth / undetected / basic | stealth（可破 Cloudflare 403） |
| CRAWLER_HEADFUL | 本地有头调试（显示浏览器窗口） | 无头 |
| PAGE_GAP_SECONDS | 页间冷却秒数 | 60 |

发邮件用 Python 标准库（smtplib + email）实现，零额外依赖。SMTP 服务器按发件邮箱域名自动推断（qq/foxmail/163/126/gmail/outlook/hotmail），不在列表里就设 `SMTP_HOST` 和 `SMTP_PORT`（465 走 SSL，587 走 STARTTLS）。

## 反爬说明

SocialBlade 对无头浏览器与数据中心 IP 都有封锁，脚本内置三层应对：

1. `stealth` 层（默认）：playwright-stealth 指纹修补 + `magic`（navigator 覆盖、随机 UA、模拟交互）+ 1920x1080 真实视口。实测默认无头模式被 Cloudflare 403，加上这一层即恢复 200。
2. 独立会话 + 页间冷却：每个排名页用全新浏览器会话，间隔 60s（`PAGE_GAP_SECONDS` 可调）；Cloudflare 的 JS challenge 需要按分钟级冷却才解除，因此反爬失败的重试从 180s 起步。
3. `undetected` 层：stealth 仍被拦时，`CRAWLER_MODE=undetected` 切换 patchright 适配器（补丁版 Chromium，去除 CDP 特征）。

排查原始状态可用 `CRAWLER_MODE=basic`（关闭全部伪装，仅看默认行为是否被拦）。

## 自动化

GitHub Actions 工作流已经写好（`.github/workflows/python_application.yml`），每个月2号凌晨2点自动运行一次，用 `uv sync --frozen` 按 `uv.lock` 精确还原环境，和本地开发环境完全一致。

## 免责声明

这玩意儿就是个玩具，别指望它有多稳定。要是哪天不能用了，也别来找我，我可能早就忘了这回事了。
