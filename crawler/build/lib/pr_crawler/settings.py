# -*- coding: utf-8 -*-
import os

# -------------- 基本 --------------
BOT_NAME = "pr_crawler"
SPIDER_MODULES = ["pr_crawler.spiders"]
NEWSPIDER_MODULE = "pr_crawler.spiders"

DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

LOG_LEVEL = "INFO"
LOG_SHORT_NAMES = True
TELNETCONSOLE_ENABLED = False
FEED_EXPORT_ENCODING = "utf-8"
DOWNLOAD_TIMEOUT = 30

# -------------- Playwright --------------
# 基于 asyncio 的 Reactor（Windows 下更稳）
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 30_000
PLAYWRIGHT_LAUNCH_OPTIONS = {"headless": True}
PLAYWRIGHT_ABORT_RESOURCE_TYPES = ["image", "media", "font"]

# -------------- Pipelines --------------
ITEM_PIPELINES = {
    "pr_crawler.pipelines.NormalizeFieldsPipeline": 100,
    "pr_crawler.pipelines.KeywordMatchPipeline":   200,
    "pr_crawler.pipelines.DedupPipeline":          300,
    "pr_crawler.pipelines.SQLiteStorePipeline":    800,
}

# 可选项（关键词匹配的控制）
KW_ALLOW_EMPTY   = True     # item 没有 keywords 时是否放行
KW_FALLBACK_BODY = True     # 标题/摘要不命中时是否用正文字段兜底

# 指定数据库路径（与后端导出一致）
ITEMS_DB_PATH = os.environ.get("NEWS_DB_PATH") or r"scripts\windows\var\data\items.sqlite3"

# -------------- Dedupe 配置 --------------
DEDUP_UNIQUE_KEY = "url"
# 默认写到 <PR_RUNTIME_DIR or CWD>/var/seen_urls.sqlite3
PR_RUNTIME_DIR = os.environ.get("PR_RUNTIME_DIR") or os.getcwd()
DEDUP_SQLITE_PATH = os.path.join(PR_RUNTIME_DIR, "var", "seen_urls.sqlite3")
DEDUP_CASE_INSENSITIVE = True
# -------------- 并发与重试（按需） --------------
CONCURRENT_REQUESTS = 8
RETRY_TIMES = 3
DOWNLOAD_DELAY = 0.2
AUTOTHROTTLE_TARGET_CONCURRENCY = 4.0
ROBOTSTXT_OBEY = False

DOWNLOADER_MIDDLEWARES = {
    'pr_crawler.middlewares.RequestOptimizationMiddleware': 540,
    'pr_crawler.middlewares.SmartProxyMiddleware': 550,
    'pr_crawler.middlewares.EnhancedRetryMiddleware': 560,
}
RETRY_TIMES = 3
RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524, 408, 429]
RETRY_BASE_DELAY = 0.8

