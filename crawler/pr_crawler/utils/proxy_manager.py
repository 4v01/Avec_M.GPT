import random
import time
import asyncio
import aiohttp
from typing import List, Dict
from datetime import datetime, timedelta
import logging

class ProxyPoolManager:
    def __init__(self, proxy_list: List[str], health_check_interval: int = 300):
        self.proxy_list = proxy_list
        self.healthy_proxies = set()
        self.failed_proxies = set()
        self.proxy_metrics: Dict[str, Dict] = {}
        self.health_check_interval = health_check_interval
        self.lock = asyncio.Lock()
        
    async def health_check(self):
        """定期健康检查代理"""
        while True:
            await asyncio.sleep(self.health_check_interval)
            async with self.lock:
                await self._perform_health_check()
    
    async def _perform_health_check(self):
        """执行代理健康检查"""
        test_url = "http://httpbin.org/ip"
        tasks = []
        
        for proxy in self.proxy_list:
            task = self._test_proxy(proxy, test_url)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for proxy, result in zip(self.proxy_list, results):
            if isinstance(result, BaseException):
                # 处理异常情况
                self.failed_proxies.add(proxy)
                if proxy in self.healthy_proxies:
                    self.healthy_proxies.remove(proxy)
                if proxy in self.proxy_metrics:
                    self.proxy_metrics[proxy]['fail_count'] += 1
            else:
                success, response_time = result
                if success:
                    self.healthy_proxies.add(proxy)
                    if proxy in self.failed_proxies:
                        self.failed_proxies.remove(proxy)
                    
                    # 更新代理指标
                    if proxy not in self.proxy_metrics:
                        self.proxy_metrics[proxy] = {
                            'success_count': 0,
                            'fail_count': 0,
                            'avg_response_time': 0,
                            'last_success': None
                        }
                    
                    self.proxy_metrics[proxy]['success_count'] += 1
                    self.proxy_metrics[proxy]['avg_response_time'] = (
                        self.proxy_metrics[proxy]['avg_response_time'] * 0.7 + 
                        response_time * 0.3
                    )
                    self.proxy_metrics[proxy]['last_success'] = datetime.now()
                else:
                    self.failed_proxies.add(proxy)
                    if proxy in self.healthy_proxies:
                        self.healthy_proxies.remove(proxy)
                    
                    if proxy in self.proxy_metrics:
                        self.proxy_metrics[proxy]['fail_count'] += 1
    
    async def _test_proxy(self, proxy: str, test_url: str):
        """测试单个代理"""
        try:
            start_time = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.get(test_url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        response_time = time.time() - start_time
                        return True, response_time
        except Exception as e:
            logging.debug(f"Proxy {proxy} failed: {str(e)}")
        
        return False, 0
    
    def get_best_proxy(self):
        """获取最佳代理基于响应时间和成功率"""
        if not self.healthy_proxies:
            return None
        
        scored_proxies = []
        for proxy in self.healthy_proxies:
            metrics = self.proxy_metrics.get(proxy, {})
            success_rate = metrics.get('success_count', 0) / max(
                metrics.get('success_count', 0) + metrics.get('fail_count', 1), 1
            )
            response_time = metrics.get('avg_response_time', 1.0)
            
            # 评分公式：成功率权重0.6，响应时间权重0.4
            score = success_rate * 0.6 + (1 / max(response_time, 0.1)) * 0.4
            scored_proxies.append((proxy, score))
        
        scored_proxies.sort(key=lambda x: x[1], reverse=True)
        return scored_proxies[0][0]
    
    def get_random_proxy(self):
        """随机获取健康代理"""
        if not self.healthy_proxies:
            return None
        return random.choice(list(self.healthy_proxies))
    
    def report_proxy_failure(self, proxy: str):
        """报告代理失败"""
        if proxy in self.healthy_proxies:
            self.healthy_proxies.remove(proxy)
        self.failed_proxies.add(proxy)