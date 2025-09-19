import json
import logging
import urllib.parse
from typing import Iterator
import scrapy
from .base_search import BaseNewsSpider, clean_text

logger = logging.getLogger(__name__)

class SohuSearchSpider(BaseNewsSpider):
    name = "sohu_search"
    allowed_domains = ["sohu.com", "search.sohu.com", "www.sohu.com"]

    custom_settings = {
        **(BaseNewsSpider.custom_settings or {}),
        'CONCURRENT_REQUESTS': 6,
        'DOWNLOAD_DELAY': 1.0,
    }

    def __init__(self, page_max: str = "5", *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self.page_max = max(1, min(int(page_max), 10))
        except:
            self.page_max = 5

    def start_requests(self) -> Iterator[scrapy.Request]:
        if not self.keywords:
            logger.info("No keywords provided")
            return
        
        base = "https://search.sohu.com/?"
        for page in range(1, self.page_max + 1):
            params = {"query": self.keywords, "page": str(page)}
            url = base + urllib.parse.urlencode(params)
            
            yield scrapy.Request(
                url,
                headers=self._rand_headers(),
                callback=self.parse_search,
                meta={'page': page},
                dont_filter=True
            )

    def parse_search(self, response):
        page = response.meta.get('page', 1)
        logger.info(f"Parsing Sohu page {page}")
        
        # 检查响应类型
        ctype = response.headers.get('Content-Type', b'').decode('utf-8', 'ignore')
        
        item_count = 0
        
        # JSON响应处理
        if 'json' in ctype.lower():
            try:
                data = json.loads(response.text)
                items_list = None
                
                for key in ['news', 'data', 'items', 'list', 'result']:
                    if key in data and isinstance(data[key], list):
                        items_list = data[key]
                        break
                
                if items_list:
                    for row in items_list:
                        item = self.process_json_item(row)
                        if item:
                            item_count += 1
                            yield item
            except Exception as e:
                logger.warning(f"JSON parse error: {e}")
        
        # HTML响应处理
        else:
            # 多种选择器策略
            selectors = [
                'div.news-box',
                'div.vrwrap',
                'div[class*="result"]',
                'li[class*="item"]',
                'article'
            ]
            
            for selector in selectors:
                nodes = response.css(selector)
                if nodes:
                    logger.info(f"Found {len(nodes)} items with selector: {selector}")
                    break
            else:
                nodes = []
            
            for node in nodes:
                item = self.extract_html_item(node, response.url)
                if item:
                    item_count += 1
                    yield item
        
        logger.info(f"Sohu page {page} extracted {item_count} items")

    def process_json_item(self, row):
        """处理JSON格式的新闻项"""
        title = clean_text(row.get('title') or '')
        url = (row.get('url') or row.get('link') or '').strip()
        
        if not url:
            return None
        
        src = clean_text(row.get('media') or row.get('source') or '搜狐新闻')
        pub = clean_text(row.get('time') or row.get('publishTime') or '')
        summary = clean_text(row.get('summary') or row.get('intro') or '')
        
        if not self.is_relevant(f"{title} {summary}"):
            return None
        
        item = self.create_item(
            title=title or url,
            url=url,
            source=src,
            date_str=pub,
            summary=summary,
            site='sohu.com'
        )
        
        return item if self.validate_item(item) else None

    def extract_html_item(self, node, base_url):
        """从HTML节点提取新闻项"""
        title = ""
        for sel in ['h3 a::text', 'h2 a::text', '.title a::text', 'a::text']:
            title = self.safe_extract(node, sel)
            if title:
                break
        
        link = ""
        for sel in ['h3 a::attr(href)', 'h2 a::attr(href)', 'a::attr(href)']:
            link = self.safe_extract(node, sel)
            if link:
                break
        
        if not (title or link):
            return None
        
        link = self.safe_url(base_url, link)
        if not link or 'javascript' in link.lower():
            return None
        
        pub = ""
        for sel in ['.time::text', '.date::text', 'time::text']:
            pub = self.safe_extract(node, sel)
            if pub:
                break
        
        src = self.safe_extract(node, '.source::text') or '搜狐新闻'
        summary = self.safe_extract(node, '.summary::text') or \
                self.safe_extract(node, '.desc::text')
        
        if not self.is_relevant(f"{title} {summary}"):
            return None
        
        item = self.create_item(
            title=title or link,
            url=link,
            date_str=pub,
            source=src,
            summary=summary,
            site='sohu.com'
        )
        
        return item if self.validate_item(item) else None