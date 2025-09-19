import scrapy

class NewsItem(scrapy.Item):
    """新闻数据项 - 统一字段定义"""
    title = scrapy.Field()
    url = scrapy.Field()
    source = scrapy.Field()
    published = scrapy.Field()
    published_at = scrapy.Field()  # orchestrator期待的主要日期字段
    summary = scrapy.Field()
    raw_site = scrapy.Field()
    
    # 兼容字段
    date = scrapy.Field()
    pub_date = scrapy.Field()
    time = scrapy.Field()
    keywords = scrapy.Field()
    site = scrapy.Field()
    paper = scrapy.Field()
    content = scrapy.Field()
