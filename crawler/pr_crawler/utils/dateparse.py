# -*- coding: utf-8 -*-
# crawler/pr_crawler/utils/dateparse.py
import re
from datetime import datetime, timedelta

_DATE_REGEXES = [
    r'(?P<y>20\d{2})[-/年\.](?P<m>0?[1-9]|1[0-2])[-/月\.](?P<d>0?[1-9]|[12]\d|3[01])(?:\s+(?P<h>[01]?\d|2[0-3]):(?P<mi>\d{2})(?::(?P<s>\d{2}))?)?',
    r'(?P<m>0?[1-9]|1[0-2])[-/\.](?P<d>0?[1-9]|[12]\d|3[01])[-/\.](?P<y>20\d{2})(?:\s+(?P<h>[01]?\d|2[0-3]):(?P<mi>\d{2})(?::(?P<s>\d{2}))?)?',
]
def _safe_int(x, d=0):
    try: return int(x)
    except: return d

def normalize_date(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def _coerce_dt(y, m, d, h='00', mi='00', s='00'):
    y=_safe_int(y); m=_safe_int(m); d=_safe_int(d)
    h=_safe_int(h); mi=_safe_int(mi); s=_safe_int(s)
    try:
        return datetime(y,m,d,h,mi,s)
    except:
        try:
            return datetime(y,m,d)+timedelta(hours=h,minutes=mi,seconds=s)
        except:
            return None

def parse_any_date(text: str):
    if not text: return None
    t = text.strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S%z','%Y-%m-%dT%H:%M:%S','%Y-%m-%d %H:%M:%S','%Y/%m/%d %H:%M:%S','%Y-%m-%d','%Y/%m/%d'):
        try: return datetime.strptime(t.replace('Z',''), fmt)
        except: pass
    for rgx in _DATE_REGEXES:
        m = re.search(rgx, t)
        if m:
            gd = m.groupdict()
            dt = _coerce_dt(gd.get('y'), gd.get('m'), gd.get('d'), gd.get('h') or '00', gd.get('mi') or '00', gd.get('s') or '00')
            if dt: return dt
    return None

def parse_input_boundary(s: str):
    if not s: return None
    s = s.strip().replace('.', '-').replace('/', '-').replace('年','-').replace('月','-').replace('日','')
    try:
        parts = [int(x) for x in re.split(r'[-/]', s) if x]
        if len(parts)==3: return datetime(parts[0], parts[1], parts[2], 0, 0, 0)
        if len(parts)==1: return datetime(parts[0], 1, 1)
    except: pass
    return parse_any_date(s)

def within_range(dt: datetime, date_from: str='', date_to: str='') -> bool:
    if not isinstance(dt, datetime): return False
    lo = parse_input_boundary(date_from)
    hi = parse_input_boundary(date_to)
    if lo and dt < lo: return False
    if hi and dt > (hi + timedelta(days=1) - timedelta(seconds=1)): return False
    return True
