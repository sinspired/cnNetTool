import logging
import re
from datetime import datetime

import requests


class DNSResolver:
    def __init__(self):
        self.dns_records = {}

    def save_hosts_cache(self):
        # 模拟保存缓存方法
        logging.debug("保存缓存到本地")

    def resolve_via_ipaddress(self, domain: str):
        """
        使用 requests 替代 httpx 解析域名。
        """
        ips = set()
        url = f"https://dns.google/resolve?name={domain}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.121 Safari/537.36",
            "Referer": "https://www.ipaddress.com",
        }

        try:
            response = requests.get(
                url, headers=headers, timeout=1.0, allow_redirects=True
            )

            # 检查状态码
            response.raise_for_status()

            content = response.text
            print(content)

            # 匹配 IPv4 和 IPv6 地址
            ipv4_pattern = r"((?:[0-9]{1,3}\.){3}[0-9]{1,3})\b"
            ipv6_pattern = r"((?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}|[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,5}::[0-9a-fA-F]{1,6})"

            ipv4_ips = set(re.findall(ipv4_pattern, content))
            ipv6_ips = set(re.findall(ipv6_pattern, content))

            ips.update(ipv4_ips)
            ips.update(ipv6_ips)

            # 更新缓存
            current_time = datetime.now().isoformat()
            if ips:
                self.dns_records[domain] = {
                    "last_update": current_time,
                    "ipv4": list(ipv4_ips),
                    "ipv6": list(ipv6_ips),
                    "source": "DNS_records",
                }
                self.save_hosts_cache()
                logging.debug(
                    f"通过 ipaddress.com 成功解析 {domain} 并更新 DNS_records 缓存"
                )
                logging.debug(f"DNS_records：\n {ips}")
            else:
                self.dns_records[domain] = {
                    "last_update": current_time,
                    "ipv4": [],
                    "ipv6": [],
                    "source": "DNS_records",
                }
                self.save_hosts_cache()
                logging.warning(
                    f"ipaddress.com 未解析到 {domain} 的 DNS_records 地址，已写入空地址到缓存"
                )
        except requests.HTTPError as e:
            logging.error(f"HTTP 错误: {e.response.status_code} {e.request.url}")
        except requests.RequestException as e:
            logging.error(f"解析失败: {e}")
        return ips


# 测试解析域名
if __name__ == "__main__":
    resolver = DNSResolver()
    domain = "alive.github.com"
    result = resolver.resolve_via_ipaddress(domain)
    print(f"{domain} 的解析结果: {result}")
