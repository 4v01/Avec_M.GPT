# -*- coding: utf-8 -*-
import urllib.parse
import logging
import re
from typing import Iterator
import scrapy
from datetime import datetime, timedelta
from .base_search import BaseNewsSpider, NewsItem, clean_text

logger = logging.getLogger(__name__)

class SogouWeixinSpider(BaseNewsSpider):
    name = "sogou_weixin"
    allowed_domains = ["weixin.sogou.com", "sogou.com"]

    custom_settings = {
        **(BaseNewsSpider.custom_settings or {}),
        'CONCURRENT_REQUESTS': 3,  # 微信搜索更敏感，进一步降低并发
        'DOWNLOAD_DELAY': 2.0,
        'RETRY_TIMES': 3,
        'RANDOMIZE_DOWNLOAD_DELAY': True,  # 随机化延迟
    }

    def __init__(self, max_pages: str = "3", year_filter: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self.max_pages = max(1, min(int(max_pages), 5))
        except:
            self.max_pages = 3
        
        # 年份筛选，默认使用当前年份
        self.year_filter = year_filter or str(datetime.now().year)

    def start_requests(self) -> Iterator[scrapy.Request]:
        if not self.keywords:
            logger.info("no keywords -> skip")
            return
        
        # 根据您的研究，添加年份筛选以提高相关性
        # https://weixin.sogou.com/weixin?type=2&s_from=input&query=聚龙湾+2025
        search_query = f"{self.keywords} {self.year_filter}"
        
        base = "https://weixin.sogou.com/weixin"
        
        for page in range(1, self.max_pages + 1):
            params = {
                'type': '2',  # 文章搜索
                's_from': 'input',
                'query': search_query
            }
            
            # 第二页及以后需要添加page参数
            if page > 1:
                params['page'] = str(page)
            
            url = base + "?" + urllib.parse.urlencode(params)
            
            yield scrapy.Request(
                url,
                headers=self._get_weixin_headers(),
                callback=self.parse_weixin_results,
                meta={'page': page, 'search_query': search_query},
                dont_filter=True
            )

    def parse_weixin_results(self, response):
        """解析微信搜索结果"""
        page = response.meta.get('page', 1)
        logger.info(f"解析搜狗微信第 {page} 页: {response.url}")
        
        # 检查是否遇到验证码或反爬机制
        if self._check_anti_crawler(response):
            logger.warning(f"搜狗微信第 {page} 页遇到反爬限制")
            return
        
        # 微信文章结果容器
        result_selectors = [
            'div.news-list div.news-box',
            'ul.news-list li',
            'div.results div.result',
            'div[class*="news-box"]'
        ]
        
        results = []
        for selector in result_selectors:
            results = response.css(selector)
            if results:
                logger.info(f"使用选择器 '{selector}' 找到 {len(results)} 个微信文章")
                break
        
        if not results:
            logger.warning(f"第 {page} 页未找到微信文章结果")
            return
        
        item_count = 0
        for result in results:
            # 提取文章标题和链接
            title = ""
            link = ""
            
            # 微信文章标题选择器
            title_selectors = [
                'h3 a::text', 'h4 a::text', '.tit a::text', 
                'a[uigs*="title"]::text', '.news-tit::text'
            ]
            
            for sel in title_selectors:
                title = self.safe_extract(result, sel)
                if title:
                    break
            
            # 微信文章链接选择器
            link_selectors = [
                'h3 a::attr(href)', 'h4 a::attr(href)', 
                '.tit a::attr(href)', 'a[uigs*="title"]::attr(href)'
            ]
            
            for sel in link_selectors:
                link = self.safe_extract(result, sel)
                if link:
                    break
            
            if not (title or link):
                continue
            
            # 处理链接
            link = self._process_weixin_link(response, link)
            if not link:
                continue
            
            # 提取公众号名称
            account_name = ""
            account_selectors = [
                '.s-p a::text', '.account::text', 
                '[class*="account"]::text', '.source::text'
            ]
            for sel in account_selectors:
                account_name = self.safe_extract(result, sel)
                if account_name:
                    break
            
            # 提取摘要
            summary = ""
            summary_selectors = [
                '.s-p::text', '.txt-info::text', '.content::text', 'p::text'
            ]
            for sel in summary_selectors:
                summary = self.safe_extract(result, sel)
                if summary and len(summary) > 10:
                    break
            
            # 提取发布时间
            pub_time = ""
            time_selectors = [
                '.s-p .time::text', '.time::text', '[class*="time"]::text'
            ]
            for sel in time_selectors:
                pub_time = self.safe_extract(result, sel)
                if pub_time:
                    break
            
            # 处理时间格式
            processed_time = self._process_weixin_time(pub_time)
            
            # 验证关键词相关性
            if not self.is_relevant(f"{title} {summary}"):
                continue
            
            # 检查是否为有效的公众号文章
            if not self._is_valid_weixin_article(title, account_name, link):
                continue
            
            source = f"微信公众号-{account_name}" if account_name else "微信公众号"
            
            item = self.create_item(
                title=title,
                url=link,
                date=processed_time,
                source=source,
                summary=summary,
                site='weixin.sogou.com'
            )
            
            if self.validate_item(item):
                item_count += 1
                yield item
        
        logger.info(f"搜狗微信第 {page} 页提取 {item_count} 条文章")

    def _check_anti_crawler(self, response) -> bool:
        """检查反爬机制"""
        # 检查验证码页面
        if '验证码' in response.text or 'antispider' in response.text.lower():
            return True
        
        # 检查是否返回空结果页面
        if '没有找到相关的微信公众号文章' in response.text:
            return False  # 这是正常的无结果情况
        
        # 检查状态码
        if response.status != 200:
            return True
        
        return False

    def _process_weixin_link(self, response, raw_link: str) -> str:
        """处理微信文章链接"""
        if not raw_link:
            return ""
        
        # 如果是完整链接，直接返回
        if raw_link.startswith('http'):
            return raw_link
        
        # 如果是相对链接，拼接完整URL
        if raw_link.startswith('/'):
            return response.urljoin(raw_link)
        
        return raw_link

    def _process_weixin_time(self, time_str: str) -> str:
        """处理微信文章时间格式"""
        if not time_str:
            return ""
        
        # 常见格式：2025-01-15、1月15日、昨天、3天前等
        
        # 标准日期格式
        if re.match(r'\d{4}-\d{2}-\d{2}', time_str):
            return time_str
        
        # 中文日期格式：1月15日
        month_day_match = re.search(r'(\d{1,2})月(\d{1,2})日', time_str)
        if month_day_match:
            month, day = month_day_match.groups()
            year = self.year_filter
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        
        # 相对时间：昨天、3天前等
        if '昨天' in time_str:
            yesterday = datetime.now() - timedelta(days=1)
            return yesterday.strftime('%Y-%m-%d')
        
        # N天前
        days_ago_match = re.search(r'(\d+)天前', time_str)
        if days_ago_match:
            days = int(days_ago_match.group(1))
            date = datetime.now() - timedelta(days=days)
            return date.strftime('%Y-%m-%d')
        
        return time_str

    def _is_valid_weixin_article(self, title: str, account: str, url: str) -> bool:
        """验证是否为有效的微信公众号文章"""
        # 排除明显的非文章内容
        exclude_keywords = [
            '广告', '推广', '招聘', '求职', '二手', '转让'
        ]
        
        title_lower = title.lower()
        if any(keyword in title_lower for keyword in exclude_keywords):
            return False
        
        # 检查URL是否为微信文章链接
        if url and 'mp.weixin.qq.com' in url:
            return True
        
        # 如果有公众号名称，认为是有效的
        if account and len(account.strip()) > 1:
            return True
        
        return len(title) > 5  # 标题长度基本检查

    def _get_weixin_headers(self) -> dict:
        """获取微信搜索专用请求头"""
        headers = self._rand_headers()
        headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
        })
        return headers