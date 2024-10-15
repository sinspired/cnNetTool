from ping3 import ping
import socket
import os
import shutil
import platform
from typing import List, Set, Tuple
import asyncio
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import time

start_time = datetime.now()

class HostsUpdater:
    def __init__(
        self,
        domain_sets: List[List[str]],
        ip_sets: List[Set[str]],
        num_fastest: int = 2,
    ):
        self.domain_sets = domain_sets
        self.ip_sets = ip_sets
        self.num_fastest = num_fastest
        self.hosts_file_path = self.get_hosts_file_path()

    @staticmethod
    def get_hosts_file_path() -> str:
        """
        根据操作系统获取hosts文件的路径
        """
        os_type = platform.system().lower()
        if os_type == "windows":
            return r"C:\Windows\System32\drivers\etc\hosts"
        elif os_type in ["linux", "darwin"]:
            return "/etc/hosts"
        else:
            raise ValueError("不支持的操作系统")

    async def resolve_domain(self, domain: str) -> Set[str]:
        """
        异步解析域名为IP地址集合，包括IPv4和IPv6
        """
        try:
            # 使用 getaddrinfo 获取所有地址信息
            addrinfo = await asyncio.to_thread(socket.getaddrinfo, domain, None)

            ips = set()
            for _, _, _, _, sockaddr in addrinfo:
                ip = sockaddr[0]
                # 验证IP地址的有效性
                try:
                    ipaddress.ip_address(ip)
                    ips.add(ip)
                except ValueError:
                    print(f"无效IP地址: {ip}")
            print(f"成功解析 {domain}: {ips}")
            return ips
        except Exception as e:
            print(f"解析 {domain} 时出错: {e}")
            return set()


    @staticmethod
    def is_ipv6(ip: str) -> bool:
        """
        判断IP地址是否为IPv6

        :param ip: IP地址
        :return: 是否为IPv6
        """
        return ":" in ip
    
    async def test_ip_connection(
        self, ip: str, port: int = 80, timeout: float = 1
    ) -> float:
        """
        测试IP连接速度

        :param ip: 要测试的IP地址
        :param port: 要连接的端口
        :param timeout: 超时时间
        :return: 连接时间（毫秒）
        """
        try:
            start = time.time()
            if self.is_ipv6(ip):
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            await asyncio.to_thread(sock.connect, (ip.strip("[]"), port))
            end = time.time()
            sock.close()
            return (end - start) * 1000
        except Exception as e:
            print(f"连接测试失败 {ip}: {e}")
            return float("inf")
        
    async def ping_ip(self, ip: str) -> Tuple[str, float]:
        """
        异步ping IP地址并返回响应时间，支持IPv4和IPv6
        """
        try:
            # 对于IPv6地址，需要去掉方括号
            clean_ip = ip.strip('[]')
            response_time = await asyncio.to_thread(ping, clean_ip, timeout=1)
            if response_time is not None and response_time > 0:
                print(f"IP {ip} 响应时间: {response_time:.5f} 秒")
                return ip, response_time
            else:
                print(f"IP {ip} 无响应或响应时间异常")
                return ip, float("inf")
            # if self.is_ipv6:
            #     response_time_ms = await self.test_ip_connection(ip)
            # else:
            #     result = await asyncio.to_thread(ping, ip, timeout=15)
            #     response_time_ms = result * 1000 if result is not None else float("inf")



            # response_time_ms = await self.test_ip_connection(ip)
            if response_time_ms != float("inf"):
                print(f"{ip} 响应时间: {response_time_ms:.2f} ms")
            else:
                print(f"{ip} 无响应")
            
            return ip, response_time_ms

        except Exception as e:
            print(f"Ping {ip} 时出错: {e}")
            return ip, float("inf")
        
    def ping_ips(self, ips: Set[str]) -> List[Tuple[str, float]]:
        """
        多线程Ping IP地址并返回响应时间
        """
        with ThreadPoolExecutor() as executor:
            tasks = [executor.submit(asyncio.run, self.ping_ip(ip)) for ip in ips]
            results = [task.result() for task in tasks]
        return results
    
    async def get_fastest_ips(
        self, domains: List[str], file_ips: Set[str]
    ) -> List[Tuple[str, float]]:
        """
        获取最快的IP地址列表
        """
        all_ips = set()

        print("正在解析域名...")
        # 解析域名为IP
        tasks = [self.resolve_domain(domain) for domain in domains]
        resolved_ips = await asyncio.gather(*tasks)
        for ips in resolved_ips:
            all_ips.update(ips)

        # 添加文件中的IP
        all_ips.update(file_ips)

        print(f"共找到 {len(all_ips)} 个唯一IP地址")
        print("正在ping所有IP地址...")

        # 多线程Ping所有IP
        results = self.ping_ips(all_ips)

        # 过滤掉无响应的IP
        valid_results = [(ip, time) for ip, time in results if time != float("inf")]

        # 排序并返回最快的IP
        fastest = sorted(valid_results, key=lambda x: x[1])[:self.num_fastest]
        print(f"最快的 {self.num_fastest} 个IP地址:")
        for ip, time in fastest:
            print(f"  {ip}: {time:.2f} 秒")
        return fastest

    def backup_hosts_file(self):
        """
        备份hosts文件
        """
        if os.path.exists(self.hosts_file_path):
            backup_path = f"{self.hosts_file_path}.bak"
            shutil.copy(self.hosts_file_path, backup_path)
            print(f"已备份 {self.hosts_file_path} 到 {backup_path}")

    def write_to_hosts_file(self, new_entries: List[str]):
        """
        将新条目写入hosts文件，保持适当的格式
        """
        self.backup_hosts_file()
        
        with open(self.hosts_file_path, "r") as f:
            existing_content = f.read().splitlines()
        
        # 提取新条目中的域名
        new_domains = set()
        for entry in new_entries:
            parts = entry.split()
            if len(parts) >= 2:
                new_domains.add(parts[1])
        
        # 保留不匹配新域名的现有行
        new_content = []
        skip = False
        for line in existing_content:
            line = line.strip()

            # 检查是否是旧的HostsUpdater标记行
            if line.startswith("# 以下条目由 HostsUpdater 脚本添加于"):
                skip = True
            
            # 如果是需要跳过的旧条目，继续跳过
            if skip:
                if line == "" or line.startswith("#"):
                    continue
                else:
                    skip = False

            # 处理非标记行，但排除包含“以下条目由 HostsUpdater 脚本添加”的行
            if (line.startswith("#") or not line) and "以下条目由 HostsUpdater 脚本添加" not in line:
                new_content.append(line)
                continue

            # 分割行内容，处理可能的制表符
            parts = line.split()
            if len(parts) < 2:
                new_content.append(line)
                continue

            # 提取域名（第二个元素）
            domain = parts[1]

            if domain not in new_domains:
                new_content.append(line)
            else:
                print(f"删除旧条目: {line}")

        # 添加注释行
        new_content.append(
            f"# 以下条目由 HostsUpdater 脚本添加于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        print("正在更新hosts文件...")
        for entry in new_entries:
            new_content.append(f"{entry} #mkhosts")
            print(f"添加条目: {entry}")

        # 写入新内容
        with open(self.hosts_file_path, "w") as f:
            f.write("\n".join(new_content))

    async def update_hosts(self):
        """
        更新hosts文件的主函数
        """
        all_entries = []
        for i, (domains, file_ips) in enumerate(zip(self.domain_sets, self.ip_sets), 1):
            print(f"\n处理第 {i} 组域名和IP...")
            fastest_ips = await self.get_fastest_ips(domains, file_ips)
            for domain in domains:
                all_entries.extend([f"{ip}\t{domain}" for ip, _ in fastest_ips])

        self.write_to_hosts_file(all_entries)
        print("\nhosts文件更新完成！")


async def main():
    print("启动Hosts文件更新器...")

    domain_names_set1 = [
        "tmdb.org",
        "api.tmdb.org",
        "themoviedb.org",
        "api.themoviedb.org",
        "www.themoviedb.org",
        "auth.themoviedb.org",
    ]

    ip_addresses_set1 = {
        "18.239.36.98",
        "108.160.169.178",
        "18.165.122.73",
        "13.249.146.88",
        "13.224.167.74",
        "13.249.146.96",
        "99.86.4.122",
        "108.160.170.44",
        "108.160.169.54",
        "98.159.108.58",
        "13.226.225.4",
        "31.13.80.37",
        "202.160.128.238",
        "13.224.167.16",
        "199.96.63.53",
        "104.244.43.6",
        "18.239.36.122",
        "66.220.149.32",
        "108.157.14.15",
        "202.160.128.14",
        "52.85.242.44",
        "199.59.149.207",
        "54.230.129.92",
        "54.230.129.11",
        "103.240.180.117",
        "66.220.148.145",
        "54.192.175.79",
        "143.204.68.100",
        "31.13.84.2",
        "18.239.36.64",
        "52.85.242.124",
        "54.230.129.83",
        "18.165.122.27",
        "13.33.88.3",
        "202.160.129.36",
        "108.157.14.112",
        "99.86.4.16",
        "199.59.149.237",
        "199.59.148.202",
        "54.230.129.74",
        "202.160.128.40",
        "199.16.156.39",
        "13.224.167.108",
        "192.133.77.133",
        "168.143.171.154",
        "54.192.175.112",
        "128.242.245.43",
        "54.192.175.108",
        "54.192.175.87",
        "199.59.148.229",
        "143.204.68.22",
        "13.33.88.122",
        "52.85.242.73",
        "18.165.122.87",
        "168.143.162.58",
        "103.228.130.61",
        "128.242.240.180",
        "99.86.4.8",
        "104.244.46.52",
        "199.96.58.85",
        "13.226.225.73",
        "128.121.146.109",
        "69.30.25.21",
        "13.249.146.22",
        "13.249.146.87",
        "157.240.12.5",
        "3.162.38.113",
        "143.204.68.72",
        "104.244.43.52",
        "13.224.167.10",
        "3.162.38.31",
        "3.162.38.11",
        "3.162.38.66",
        "202.160.128.195",
        "162.125.6.1",
        "104.244.43.128",
        "18.165.122.23",
        "99.86.4.35",
        "108.160.165.212",
        "108.157.14.27",
        "13.226.225.44",
        "157.240.9.36",
        "13.33.88.37",
        "18.239.36.92",
        "199.59.148.247",
        "13.33.88.97",
        "31.13.84.34",
        "124.11.210.175",
        "13.226.225.52",
        "31.13.86.21",
        "108.157.14.86",
        "143.204.68.36",
    }

    domain_names_set2 = ["image.tmdb.org", "images.tmdb.org"]

    ip_addresses_set2 = {
        "89.187.162.242",
        "169.150.249.167",
        "143.244.50.209",
        "143.244.50.210",
        "143.244.50.88",
        "143.244.50.82",
        "169.150.249.165",
        "143.244.49.178",
        "143.244.49.179",
        "143.244.50.89",
        "143.244.50.212",
        "169.150.207.215",
        "169.150.249.163",
        "143.244.50.85",
        "143.244.50.91",
        "143.244.50.213",
        "169.150.249.164",
        "169.150.249.162",
        "169.150.249.166",
        "143.244.49.183",
        "143.244.49.177",
        "143.244.50.83",
        "138.199.9.104",
        "169.150.249.169",
        "143.244.50.214",
        "79.127.213.217",
        "143.244.50.87",
        "143.244.50.84",
        "169.150.249.168",
        "143.244.49.180",
        "143.244.50.86",
        "143.244.50.90",
        "143.244.50.211",
    }

    # 新增 Google 翻译相关域名集合
    domain_names_set3 = [
        "translate.google.com",
        "translate.googleapis.com",
        "translate-pa.googleapis.com",
    ]

    ip_addresses_set3 = {
        "216.239.32.40",
        "2404:6800:4008:c15::94",
        "35.197.239.137",
        "2a00:1450:4001:829::201a",
        "2404:6800:4008:c13::5a",
        "35.186.181.189",
        "35.189.113.240",
        "35.228.168.221",
        "2a00:1450:4001:803::201a",
        "35.210.233.33",
        "74.125.204.139"
    }

    print("初始化HostsUpdater...")
    updater = HostsUpdater(
        domain_sets=[domain_names_set1, domain_names_set2, domain_names_set3],
        ip_sets=[ip_addresses_set1, ip_addresses_set2, ip_addresses_set3],
        num_fastest=2,
    )

    print("开始更新hosts文件...")
    await updater.update_hosts()
    print("操作完成。")
    end_time = datetime.now()
    elapsed_time = end_time - start_time
    print(f"代码运行时间: {elapsed_time.total_seconds():.2f} 秒")


if __name__ == "__main__":
    asyncio.run(main())
