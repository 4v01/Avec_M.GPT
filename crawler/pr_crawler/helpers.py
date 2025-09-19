import re, datetime as dt

def parse_date(text: str):
    if not text: return None
    text = text.strip()
    m = re.search(r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})', text)
    if m:
        y,mn,d = map(int, m.groups())
        try: return dt.date(y,mn,d).isoformat()
        except: return None
    return None
