# -*- coding: utf-8 -*-
import logging, random, time
from scrapy import signals
from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.downloadermiddlewares.httpproxy import HttpProxyMiddleware
from scrapy.utils.response import response_status_message

logger = logging.getLogger(__name__)

# ---- 可选依赖（存在则用，不存在则降级） ----
try:
    from .utils.proxy_manager import ProxyPoolManager  # type: ignore
except Exception:
    class ProxyPoolManager:  # type: ignore
        def __init__(self, proxy_list=None, health_check_interval: int = 300):
            self.proxy_list = proxy_list or []
        async def health_check(self):
            return
        def get_best_proxy(self, key: str = ""):
            # 简单随机；真实项目里可按 key 做一致性哈希
            return random.choice(self.proxy_list) if self.proxy_list else None

try:
    from .utils.anti_scrape import AntiAntiScrape  # type: ignore
except Exception:
    class AntiAntiScrape:  # type: ignore
        _UA = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        ]
        def random_headers(self):
            return {
                "User-Agent": random.choice(self._UA),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
            }
        def get_cookie_jar(self):
            return {}

class EnhancedRetryMiddleware(RetryMiddleware):
    def __init__(self, settings):
        super().__init__(settings)
        self.max_retry_times = settings.getint('RETRY_TIMES', 3)
        self.retry_http_codes = set(int(x) for x in settings.getlist(
            'RETRY_HTTP_CODES', [500, 502, 503, 504, 522, 524, 408, 429]
        ))
        self._base_delay = settings.getfloat('RETRY_BASE_DELAY', 0.8)  # 指数退避基数

    def process_response(self, request, response, spider):
        if response.status in self.retry_http_codes:
            reason = response_status_message(response.status)
            return self._retry_with_backoff(request, response, reason, spider) or response
        return response

    def process_exception(self, request, exception, spider):
        # 连接类、超时类异常仍走父类逻辑
        return super().process_exception(request, exception, spider)

    def _retry_with_backoff(self, request, response, reason, spider):
        # honor Retry-After
        delay = None
        try:
            ra = response.headers.get(b"Retry-After")
            if ra:
                delay = float(ra.decode("utf-8", "ignore"))
        except Exception:
            pass

        retries = request.meta.get('retry_times', 0)
        if delay is None:
            delay = self._base_delay * (2 ** retries)  # 指数退避

        if retries < self.max_retry_times:
            if delay and delay > 0:
                spider.logger.debug("retrying %s in %.2fs due to %s (status=%s)",
                                    request.url, delay, reason, response.status)
                time.sleep(min(delay, 5.0))  # 轻量 sleep，避免雪崩；如需更优雅可用延迟队列
            return self._retry(request, reason, spider)
        return None

class SmartProxyMiddleware(HttpProxyMiddleware):
    def __init__(self, proxy_pool):
        self.proxy_pool = proxy_pool

    @classmethod
    def from_crawler(cls, crawler):
        proxy_file = crawler.settings.get('PROXY_LIST_FILE', 'config/proxies.txt')
        proxies = []
        try:
            with open(proxy_file, 'r', encoding='utf-8') as f:
                proxies = [line.strip() for line in f if line.strip()]
        except Exception:
            logger.info("Proxy file not found: %s", proxy_file)
        pool = ProxyPoolManager(proxies)
        return cls(pool)

    def process_request(self, request, spider):
        if request.meta.get('dont_proxy'):
            return
        # 以 cookiejar 或 keywords 作为“粘滞 key”，尽量复用同一路由
        key = str(request.meta.get("cookiejar") or request.meta.get("kw") or "")
        proxy = getattr(self.proxy_pool, 'get_best_proxy', lambda _=None: None)(key)
        if proxy:
            request.meta['proxy'] = proxy

class RequestOptimizationMiddleware:
    def __init__(self):
        try:
            self.anti = AntiAntiScrape()
        except Exception:
            self.anti = AntiAntiScrape()

    def process_request(self, request, spider):
        # 1) UA/基础头补齐
        try:
            # 不覆盖已有 header；只补齐缺失的
            rand_hdrs = self.anti.random_headers()
            for k, v in rand_hdrs.items():
                kb = k.encode() if isinstance(k, str) else k
                if kb not in request.headers:
                    request.headers[kb] = v.encode() if isinstance(v, str) else v
        except Exception:
            pass

        # 2) 轻量 referer
        if b'Referer' not in request.headers:
            try:
                from urllib.parse import urlparse
                domain = urlparse(request.url).netloc
                request.headers[b'Referer'] = f"https://{domain}/".encode()
            except Exception:
                pass

        # 3) Cookie 注入（可选）
        try:
            jar = self.anti.get_cookie_jar()
            if jar and not getattr(request, 'cookies', None):
                request.cookies = jar
        except Exception:
            pass

        # 4) If-Modified-Since / If-None-Match（仅当上游自行塞入缓存标记）
        ims = request.meta.get("if_modified_since")
        etag = request.meta.get("if_none_match")
        if ims and b"If-Modified-Since" not in request.headers:
            request.headers[b"If-Modified-Since"] = str(ims).encode()
        if etag and b"If-None-Match" not in request.headers:
            request.headers[b"If-None-Match"] = str(etag).encode()

__all__ = [
    'EnhancedRetryMiddleware',
    'RequestOptimizationMiddleware',
    'SmartProxyMiddleware',
]
