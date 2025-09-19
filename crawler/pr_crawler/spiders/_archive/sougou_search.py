# -*- coding: utf-8 -*-
import urllib.parse as _up
import logging
import re
from typing import Iterator
import scrapy
from .base_search import BaseNewsSpider, clean_text

logger = logging.getLogger(__name__)

class SogouSearchSpider(BaseNewsSpider):
    name = "sogou_search"
    allowed_domains = ["www.sogou.com", "sogou.com"]

    custom_settings = {
        **(BaseNewsSpider.custom_settings or {}),
        'CONCURRENT_REQUESTS': 4,  # 搜狗对并发敏感，降低并发数
        'DOWNLOAD_DELAY': 1.2,
        'RETRY_TIMES': 3,
    }

    def __init__(self, max_pages: str = "3", time_filter: str = "month", *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self.max_pages = max(1, min(int(max_pages), 5))
        except:
            self.max_pages = 3
        
        # 时间筛选选项: all, day, week, month, year
        self.time_filter = time_filter

    def start_requests(self) -> Iterator[scrapy.Request]:
        if not self.keywords:
            logger.info("no keywords -> skip")
            return
        
        # 根据您的研究，搜索"关键词+新闻"以提高相关性
        search_query = f"{self.keywords} 新闻"
        
        # 基础搜索URL
        base = "https://www.sogou.com/web"
        
        for page in range(1, self.max_pages + 1):
            params = {
                'query': search_query,
                'page': str(page) if page > 1 else None  # 第一页不需要page参数
            }
            
            # 移除None值
            params = {k: v for k, v in params.items() if v is not None}
            
            url = base + "?" + _up.urlencode(params)
            
            yield self.req(
                url,
                callback=self.parse_search_page,
                headers=self._get_sogou_headers(),
                meta={'page': page, 'search_query': search_query}
            )

    def parse_search_page(self, response):
        """解析搜索结果页，寻找时间筛选链接"""
        page = response.meta.get('page', 1)
        search_query = response.meta.get('search_query', '')
        
        logger.info(f"解析搜狗第 {page} 页: {response.url}")
        
        # 如果是第一页且需要时间筛选，先寻找时间筛选链接
        if page == 1 and self.time_filter != 'all':
            time_filter_url = self._extract_time_filter_url(response, search_query)
            if time_filter_url:
                logger.info(f"找到时间筛选链接: {time_filter_url}")
                yield self.req(
                    time_filter_url,
                    callback=self.parse_results,
                    meta={'page': page, 'filtered': True}
                )
                return
        
        # 解析普通搜索结果
        yield from self.parse_results(response)

    def _extract_time_filter_url(self, response, search_query: str) -> str:
        """提取时间筛选链接"""
        # 根据您提供的HTML结构查找时间筛选链接
        time_bar = response.css('div.top-bar-content')
        if not time_bar:
            return ""
        
        time_filter_map = {
            'day': '一天内',
            'week': '一周内', 
            'month': '一月内',
            'year': '一年内'
        }
        
        target_text = time_filter_map.get(self.time_filter, '')
        if not target_text:
            return ""
        
        # 查找对应的时间筛选链接
        for link in time_bar.css('a'):
            link_text = clean_text(link.css('::text').get() or '')
            if target_text in link_text:
                href = link.css('::attr(href)').get()
                if href:
                    return response.urljoin(href)
        
        return ""

    def parse_results(self, response):
        """解析搜索结果"""
        page = response.meta.get('page', 1)
        filtered = response.meta.get('filtered', False)
        
        logger.info(f"解析搜狗{'筛选后' if filtered else ''}结果第 {page} 页")
        
        # 搜索结果选择器（搜狗的结果结构）
        result_selectors = [
            'div.results div.vrwrap',  # 主要结果容器
            'div.results div.rb', 
            'div.result',
            'div[class*="result"]'
        ]
        
        results = []
        for selector in result_selectors:
            results = response.css(selector)
            if results:
                logger.info(f"使用选择器 '{selector}' 找到 {len(results)} 个结果")
                break
        
        if not results:
            logger.warning(f"第 {page} 页未找到搜索结果")
            self.dump_on_empty(response, reason=f"sogou_p{page}_no_results")
            return
        
        item_count = 0
        for result in results:
            # 跳过广告
            if self._is_advertisement(result):
                continue
            
            # 提取标题和链接
            title = ""
            link = ""
            
            # 标题选择器
            title_selectors = [
                'h3 a::text', 'h3 a', '.vr-title a::text', 
                'a[target="_blank"]::text', '.title::text'
            ]
            
            for sel in title_selectors:
                if sel.endswith('::text'):
                    title = self.safe_extract(result, sel)
                else:
                    title_elem = result.css(sel)
                    if title_elem:
                        title = clean_text(title_elem.css('::text').get())
                if title:
                    break
            
            # 链接选择器
            link_selectors = [
                'h3 a::attr(href)', '.vr-title a::attr(href)',
                'a[target="_blank"]::attr(href)'
            ]
            
            for sel in link_selectors:
                link = self.safe_extract(result, sel)
                if link:
                    break
            
            if not (title and link):
                continue
            
            link = self.safe_url(response.url, link)
            if not self.is_valid_news_url(link):
                continue
            
            # 提取摘要
            summary = ""
            summary_selectors = ['.str_content::text', '.str-content::text', '.abstract::text', 'p::text']
            for sel in summary_selectors:
                summary = self.safe_extract(result, sel)
                if summary and len(summary) > 15:
                    break
            
            # 提取时间信息
            date_text = ""
            date_selectors = ['.str_time::text', '.time::text', '[class*="time"]::text']
            for sel in date_selectors:
                date_text = self.safe_extract(result, sel)
                if date_text:
                    break
            
            # 提取来源
            source = ""
            source_selectors = ['.str_info a::text', '.source::text', '[class*="source"]::text']
            for sel in source_selectors:
                source = self.safe_extract(result, sel)
                if source:
                    break
            
            # 验证关键词相关性（搜狗广告较多，严格筛选）
            if not self.is_relevant(f"{title} {summary}"):
                continue
            
            # 额外的新闻相关性检查
            if not self._is_news_related(title, summary, link):
                continue
            
            item = self.make_item(
                title=title or link,
                url=link,
                published_at=date_text,
                source=source or '搜狗搜索',
                summary=summary,
                site='sogou.com'
            )
            
            if self.validate(item):
                item_count += 1
                yield item
        
        logger.info(f"搜狗第 {page} 页提取 {item_count} 条新闻")

        # 处理分页
        if item_count > 0 and page < self.max_pages:
            # 寻找下一页链接
            next_selectors = [
                'a.n::attr(href)',  # 搜狗的下一页链接
                'a:contains("下一页")::attr(href)',
                '.page a:contains("下一页")::attr(href)'
            ]
            
            for sel in next_selectors:
                next_href = response.css(sel).get()
                if next_href:
                    next_url = response.urljoin(next_href)
                    yield self.req(
                        next_url,
                        callback=self.parse_results,
                        meta={'page': page + 1, 'filtered': filtered}
                    )
                    break

    def _is_advertisement(self, result_element) -> bool:
        """检测是否为广告"""
        # 检查是否包含广告标识
        ad_indicators = [
            'ad', '广告', 'sponsor', '推广', 'ec_', 'tuiguang'
        ]
        
        result_html = result_element.get()
        if any(indicator in result_html.lower() for indicator in ad_indicators):
            return True
        
        # 检查class名称
        class_names = ' '.join(result_element.css('::attr(class)').getall()).lower()
        if any(indicator in class_names for indicator in ad_indicators):
            return True
        
        return False

    def _is_news_related(self, title: str, summary: str, url: str) -> bool:
        """检查是否为新闻相关内容"""
        # 新闻网站域名白名单
        news_domains = [
            'news.', '.news', 'xinhua', 'people', 'china', 'sina', 'sohu', 
            'qq.com', '163.com', 'ifeng', 'thepaper', 'caixin', 'eastday',
            'dayoo', 'southcn', 'ycwb', 'gzdaily'
        ]
        
        # 检查URL是否为新闻域名
        if any(domain in url.lower() for domain in news_domains):
            return True
        
        # 检查标题和摘要是否包含新闻相关词汇
        news_keywords = [
            '新闻', '报道', '消息', '通讯', '快讯', '公告', '发布',
            '记者', '采访', '报告', '发言', '表示', '透露'
        ]
        
        text = f"{title} {summary}".lower()
        news_count = sum(1 for keyword in news_keywords if keyword in text)
        
        return news_count >= 1

    def _get_sogou_headers(self) -> dict:
        """获取搜狗专用请求头"""
        headers = dict(self._headers)
        headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Referer': 'https://www.sogou.com/',
        })
        return headers