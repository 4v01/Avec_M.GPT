import random
import time
from typing import Dict, List
from fake_useragent import UserAgent
import hashlib

class AntiAntiScrape:
    def __init__(self):
        self.ua = UserAgent()
        self.request_delays: Dict[str, float] = {}
        
    def random_headers(self) -> Dict[str, str]:
        """生成随机请求头"""
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    
    def get_delay(self, domain: str, base_delay: float = 1.0) -> float:
        """获取智能延迟时间"""
        current_time = time.time()
        last_request = self.request_delays.get(domain, 0)
        
        elapsed = current_time - last_request
        if elapsed < base_delay:
            return base_delay - elapsed
        
        # 随机波动
        jitter = random.uniform(-0.2, 0.2)
        return max(0.1, base_delay + jitter)
    
    def update_request_time(self, domain: str):
        """更新请求时间"""
        self.request_delays[domain] = time.time()
    
    def generate_fingerprint(self) -> str:
        """生成浏览器指纹"""
        components = [
            str(random.randint(1000, 9999)),  # 随机组件
            str(int(time.time() * 1000))[-6:],  # 时间戳
            hashlib.md5(self.ua.random.encode()).hexdigest()[:8]  # UA哈希
        ]
        return '-'.join(components)
    
    def get_cookie_jar(self) -> Dict[str, str]:
        """生成初始Cookie"""
        return {
            'session_id': hashlib.md5(str(time.time()).encode()).hexdigest()[:16],
            'fingerprint': self.generate_fingerprint(),
            'timezone': '8',  # 东八区
            'resolution': '1920x1080'
        }

class ContentHasher:
    @staticmethod
    def generate_hash(content: str, algorithm: str = 'md5') -> str:
        """生成内容哈希"""
        if algorithm == 'md5':
            return hashlib.md5(content.encode()).hexdigest()
        elif algorithm == 'sha256':
            return hashlib.sha256(content.encode()).hexdigest()
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")
    
    @staticmethod
    def similarity_hash(content: str) -> str:
        """生成相似性哈希（SimHash）"""
        from simhash import Simhash
        return str(Simhash(content).value)