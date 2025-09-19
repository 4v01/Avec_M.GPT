import json, re, urllib.parse
import scrapy
from w3lib.html import remove_tags

RAIN_LINK_RE = re.compile(r'https?://news\.qq\.com/(?:[^"\']*/)?rain/a/\d{8}[A-Z0-9]{8}\d{2}', re.I)

class TencentNewsPCSpider(scrapy.Spider):
    name = "tencent_news_pc"
    allowed_domains = ["news.qq.com", "i.news.qq.com"]

    custom_settings = {
        # 不全局打开 Playwright，必要时再用 meta={"playwright": True}
        "DOWNLOAD_TIMEOUT": 15,
        "DNS_TIMEOUT": 5,
        "COOKIES_ENABLED": False,
        "RETRY_ENABLED": False,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Referer": "https://news.qq.com/",
        },
    }

    def __init__(self, query=None, pages=2, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.query = (query or "").strip()
        self.pages = int(pages or 2)

    # ✅ 关键：提供同步的 start_requests，避免卡在引擎未 open 的状态
    def start_requests(self):
        if not self.query:
            self.logger.info("no query provided")
            return
        # 1) 先请求 PC 搜索页（不执行 JS），尝试从首屏注入的 state 或 HTML 直接提取文章链接
        for p in range(1, self.pages + 1):
            url = f"https://news.qq.com/search?{urllib.parse.urlencode({'query': self.query, 'page': p})}"
            yield scrapy.Request(
                url,
                callback=self.parse_search_html,
                errback=self.on_req_err,
                dont_filter=True,
                meta={"page": p}
            )

        # 2) 备用：尝试官方 TRPC JSON 接口（若可用），参数可能变动，失败就忽略
        for p in range(self.pages):
            api = "https://i.news.qq.com/trpc.qqnews_web.kv_srv.kv_srv_http_proxy/search"
            payload = {
                "keyword": self.query,
                "page": p,
                "num": 20,
                "expIds": [],
                "tab": "news",
            }
            yield scrapy.Request(
                api,
                method="POST",
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": "https://news.qq.com",
                    "Referer": "https://news.qq.com/",
                },
                callback=self.parse_trpc_json,
                errback=self.on_req_err,
                dont_filter=True,
                meta={"page": p, "handle_httpstatus_all": True},
            )

    # 同时保留 async start（可要可不要）；Scrapy 2.13+ 会优先用它
    async def start(self):
        # 直接复用同步版，提升兼容性
        for req in self.start_requests():
            yield req

    def on_req_err(self, failure):
        self.logger.warning(f"request error @ {failure.request.url} -> {failure.value}")

    def parse_search_html(self, response: scrapy.http.Response):
        html = response.text
        page = response.meta.get("page")
        # ① 优先：从页面内注入的 JSON state 中找链接
        # 常见变量名：__INITIAL_STATE__ / __NUXT__ / SSR_DATA 等，这里做宽松匹配
        m = re.search(r'window\.(?:__INITIAL_STATE__|__NUXT__|SSR_STATE)\s*=\s*(\{.*?\})\s*;', html, re.S)
        links = set()
        if m:
            try:
                data = json.loads(m.group(1))
                # 宽松地在 JSON 文本里直接把符合规则的 rain 链接扫出来（结构多变时很稳）
                links |= set(RAIN_LINK_RE.findall(json.dumps(data, ensure_ascii=False)))
            except Exception as e:
                self.logger.debug(f"parse injected state failed: {e}")

        # ② 兜底：直接从 HTML 全文里用正则扫 /rain/a/xxxx 链接
        links |= set(RAIN_LINK_RE.findall(html))

        if not links:
            self.logger.info(f"tencent html no article link match: {response.url}")
            return

        for href in sorted(links):
            yield scrapy.Request(
                href,
                callback=self.parse_detail,
                errback=self.on_req_err,
                priority=10,
                dont_filter=True,
            )

    def parse_trpc_json(self, response: scrapy.http.Response):
        # 有些环境会 403/404，这里统一做“允许并尝试解析”
        try:
            data = json.loads(response.text)
        except Exception:
            self.logger.debug(f"trpc not json @ {response.status} {response.url}")
            return

        # 宽松在 JSON 文本里直接扫 rain 链接
        for href in set(RAIN_LINK_RE.findall(json.dumps(data, ensure_ascii=False))):
            yield scrapy.Request(
                href,
                callback=self.parse_detail,
                errback=self.on_req_err,
                priority=10,
                dont_filter=True,
            )

    def parse_detail(self, response: scrapy.http.Response):
        # 文章页常见结构，先尽量用 meta/ld+json 抓时间；抓不到再从文本兜底
        title = response.xpath("//meta[@property='og:title']/@content").get() \
            or response.xpath("//title/text()").get() or ""
        title = remove_tags(title).strip()

        # 常见时间字段
        pub = (
            response.xpath("//meta[@itemprop='datePublished']/@content").get()
            or response.xpath("//meta[@name='pubdate']/@content").get()
            or response.xpath("//meta[@name='publishdate']/@content").get()
            or response.xpath("//meta[@property='article:published_time']/@content").get()
        )
        if not pub:
            # 从页面文本兜底，如：发布时间：2025-08-15 10:23
            txt = response.text
            m = re.search(r'(20\d{2}[-/年.]\d{1,2}[-/月.]\d{1,2}[日]?\s*[0-2]?\d:[0-5]\d(:[0-5]\d)?)', txt)
            pub = m.group(1) if m else None

        yield {
            "source": "Tencent",
            "url": response.url,
            "title": title,
            "published_at": pub,
        }
