import json
import urllib.parse as _up
import logging
from typing import Iterator
import scrapy
from .base_search import BaseNewsSpider, clean_text

logger = logging.getLogger(__name__)

class TencentSearchSpider(BaseNewsSpider):
    name = "tencent_search"
    allowed_domains = ["i.news.qq.com", "news.qq.com", "new.qq.com"]

    custom_settings = {
        **(BaseNewsSpider.custom_settings or {}),
        'CONCURRENT_REQUESTS': 8,
        'DOWNLOAD_DELAY': 0.6,
    }

    def __init__(self, max_pages: str = "5", *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self.max_pages = max(1, min(int(max_pages), 10))
        except:
            self.max_pages = 5
    
    def start_requests(self) -> Iterator[scrapy.Request]:
        if not self.keywords:
            logger.info("No keywords provided")
            return
        
        base_api = "https://i.news.qq.com/gw/pc_search/result"
        
        for page in range(1, self.max_pages + 1):
            params = {
                "num": "20",
                "keyword": self.keywords,
                "page": str(page)
            }
            url = base_api + "?" + _up.urlencode(params)
            
            yield self.req(
                url,
                callback=self.parse_api,
                headers=self._get_tencent_headers(),
                meta={'page': page}
            )

    def parse_api(self, response) -> Iterator:
        page = response.meta.get('page', 1)
        logger.info(f"Parsing Tencent API page {page}")
        
        try:
            data = json.loads(response.text)
        except Exception as e:
            logger.warning(f"Invalid JSON response: {e}")
            # 尝试HTML解析作为后备
            yield from self.parse_html_fallback(response)
            return
        
        # 处理API响应
        if isinstance(data, dict):
            # 查找数据列表
            items_list = None
            for key in ['data', 'list', 'secList', 'results', 'items']:
                if key in data and isinstance(data[key], list):
                    items_list = data[key]
                    break
                elif key in data and isinstance(data[key], dict):
                    # 检查嵌套结构
                    nested_data = data[key]
                    for nested_key in ['list', 'items', 'results']:
                        if nested_key in nested_data and isinstance(nested_data[nested_key], list):
                            items_list = nested_data[nested_key]
                            break
                    if items_list:
                        break
            
            if not items_list:
                logger.warning(f"No valid data structure in API response")
                self.dump_on_empty(response, reason=f"tencent_api_p{page}_no_data")
                return
            
            item_count = 0
            for item_data in items_list:
                # 如果是嵌套结构
                if isinstance(item_data, dict) and 'newsList' in item_data:
                    for news in item_data.get('newsList', []):
                        news_item = self.process_news_item(news)
                        if news_item:
                            yield news_item
                            item_count += 1
                else:
                    news_item = self.process_news_item(item_data)
                    if news_item:
                        yield news_item
                        item_count += 1
            
            logger.info(f"Tencent API page {page} extracted {item_count} items")

    def process_news_item(self, row):
        """处理单个新闻项"""
        if not isinstance(row, dict):
            return None
        
        # 提取标题
        title = clean_text(
            row.get('title') or row.get('name') or 
            row.get('headline') or row.get('text') or ''
        )
        
        # 提取URL
        url = clean_text(
            row.get('url') or row.get('vurl') or 
            row.get('link') or row.get('href') or ''
        )
        
        if not url or not title:
            return None
        
        # 标准化URL
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://news.qq.com" + url
        if not self.is_valid_news_url(url):
            return None
        
        # 提取发布时间
        pub = clean_text(
            row.get('time') or row.get('publish_time') or 
            row.get('publishTime') or row.get('date') or 
            row.get('pub_time') or ''
        )
        
        # 提取来源
        source = clean_text(
            row.get('media') or row.get('source') or 
            row.get('from') or row.get('media_name') or '腾讯新闻'
        )
        
        # 提取摘要
        summary = clean_text(
            row.get('intro') or row.get('abstract') or 
            row.get('desc') or row.get('summary') or 
            row.get('content') or ''
        )
        
        # 相关性检查
        if not self.is_relevant(f"{title} {summary}"):
            return None
        
        item = self.make_item(
            title=title,
            url=url,
            published_at=pub,
            source=source,
            summary=summary,
            site='news.qq.com'
        )
        
        return item if self.validate(item) else None

    def extract_from_json(self, data):
        """从嵌套JSON结构中递归提取新闻项"""
        if isinstance(data, list):
            for item in data:
                yield from self.extract_from_json(item)
        elif isinstance(data, dict):
            # 如果当前层包含新闻列表字段，处理其中的新闻项
            if 'newsList' in data and isinstance(data['newsList'], list):
                for news in data['newsList']:
                    news_item = self.process_news_item(news)
                    if news_item:
                        yield news_item
            else:
                # 否则递归处理所有值
                for value in data.values():
                    if isinstance(value, (list, dict)):
                        yield from self.extract_from_json(value)

    def parse_html_fallback(self, response):
        """HTML解析后备方案"""
        logger.info("Using HTML fallback parser")
        
        # 尝试提取页面中的JSON数据
        scripts = response.css('script::text').getall()
        for script in scripts:
            if any(keyword in script for keyword in ['window.__INITIAL_STATE__', 'searchResult', 'newsData']):
                # 尝试提取JSON
                json_patterns = [
                    r'window\.__INITIAL_STATE__\s*=\s*({.+?});',
                    r'searchResult\s*=\s*({.+?});',
                    r'newsData\s*=\s*({.+?});',
                    r'({.+?"title".+?})',  # 通用JSON模式
                ]
                
                for pattern in json_patterns:
                    import re
                    json_match = re.search(pattern, script, re.DOTALL)
                    if json_match:
                        try:
                            data = json.loads(json_match.group(1))
                            yield from self.extract_from_json(data)
                            return
                        except:
                            continue
        
        # 如果JSON提取失败，尝试HTML结构解析
        logger.info("JSON extraction failed, trying HTML structure parsing")
        
        # 腾讯新闻页面的常见结构
        result_selectors = [
            '.news-item',
            '.result-item', 
            '.search-result',
            'li[class*="item"]',
            'div[class*="news"]'
        ]
        
        for selector in result_selectors:
            results = response.css(selector)
            if results:
                logger.info(f"Using HTML selector: {selector} - found {len(results)} items")
                for result in results:
                    title = clean_text(result.css('a::text, h3::text, .title::text').get())
                    url = clean_text(result.css('a::attr(href)').get())
                    
                    if title and url:
                        url = self.safe_url(response.url, url)
                        if self.is_valid_news_url(url) and self.is_relevant(title):
                            item = self.make_item(
                                title=title,
                                url=url,
                                source='腾讯新闻',
                                site='news.qq.com'
                            )
                            if self.validate(item):
                                yield item
                break

    def _get_tencent_headers(self) -> dict:
        """获取腾讯专用请求头"""
        headers = dict(self._headers)
        headers.update({
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://news.qq.com/',
            'Origin': 'https://news.qq.com'
        })
        return headers