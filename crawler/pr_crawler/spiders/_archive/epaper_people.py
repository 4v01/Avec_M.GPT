import scrapy, datetime, time
from pr_crawler.items import NewsItem

class PeopleEpaperSpider(scrapy.Spider):
    name = "epaper_people"
    allowed_domains = ["paper.people.com.cn"]
    custom_settings = {"FEED_FORMAT": "jl"}

    def __init__(self, query="", start_date="", end_date="", **kwargs):
        super().__init__(**kwargs)
        self.query = (query or "").strip()
        self.start_date = start_date
        self.end_date = end_date

    def start_requests(self):
        sd = datetime.datetime.strptime(self.start_date, "%Y-%m-%d").date() if self.start_date else datetime.date.today()
        ed = datetime.datetime.strptime(self.end_date, "%Y-%m-%d").date() if self.end_date else sd
        if sd > ed: sd, ed = ed, sd
        d = sd
        while d <= ed:
            y = d.strftime("%Y"); m = d.strftime("%m"); day = d.strftime("%d")
            for u in [
                f"https://paper.people.com.cn/rmrb/html/{y}-{m}/{day}/nbs.D110000renmrb_01.htm",
                f"https://paper.people.com.cn/rmrb/pc/layout/{y}{m}/{day}/node_01.html",
            ]:
                yield scrapy.Request(u, callback=self.parse_index, meta={"target_date": d.isoformat(), "playwright": True})
            d += datetime.timedelta(days=1)

    async def parse_index(self, response):
        d = response.meta.get("target_date") or ""
        seen=set()
        for a in response.css("a"):
            href = a.attrib.get("href","").strip()
            title = (a.css("::text").get() or "").strip()
            if not href or not title: continue
            url = response.urljoin(href)
            if "node_" in url or url.lower().endswith(".pdf"): continue
            if self.query and self.query.lower() not in title.lower(): continue
            if url in seen: continue
            seen.add(url)
            yield {"title": title, "url": url, "date": d, "source": "人民日报电子版", "site": "epaper"}
