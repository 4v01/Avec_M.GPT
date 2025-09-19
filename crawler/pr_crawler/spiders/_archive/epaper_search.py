import urllib.parse
import scrapy

EPAPER_SITES = [
    "paper.people.com.cn",
    "epaper.gmw.cn",
    "epaper.southcn.com",
    "epaper.bjnews.com.cn",
    "epaper.cqcb.com",
]

class EPaperSearchSpider(scrapy.Spider):
    name = "epaper_search"
    allowed_domains = ["chinaso.com", "www.chinaso.com"]
    custom_settings = {
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
            "https": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
        }
    }

    def __init__(self, query="", start_date="", end_date="", **kw):
        super().__init__(**kw)
        self.query = (query or "").strip()

    async def start(self):
        self.logger.info("start epaper_search q=%s", self.query)
        for site in EPAPER_SITES:
            q = urllib.parse.quote_plus(f"site:{site} {self.query}")
            url = f"https://www.chinaso.com/newssearch/all/allResults?q={q}"
            yield scrapy.Request(url, cb_kwargs={"src_site": site}, callback=self.parse_site)

    def parse_site(self, response, src_site):
        for it in response.css("div.result, li.result, div.res-item, .res-item"):
            title = " ".join(it.css("a::text").getall()).strip()
            href = it.css("a::attr(href)").get()
            date = (it.css(".date::text, .time::text, .res-time::text").get() or "").strip()
            if href:
                yield {
                    "date": date,
                    "title": title or href,
                    "source": src_site,
                    "url": response.urljoin(href),
                    "site": "epaper",
                }
