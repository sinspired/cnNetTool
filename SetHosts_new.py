import argparse
import asyncio
import ctypes
import sys
import dns.resolver
import socket
from ping3 import ping
import time
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint
import aiohttp
from retry import retry
import json
import re
import warnings
from dataclasses import dataclass, field
from typing import List, Set, Dict, Optional, Tuple
import platform
import os
import shutil
from datetime import datetime, timezone, timedelta
import logging
from pathlib import Path
from contextlib import asynccontextmanager
import dns.asyncresolver
from concurrent.futures import ThreadPoolExecutor
from functools import partial, lru_cache
from asyncio import SelectorEventLoop


console = Console()

start_time = datetime.now()

RESOLVER_TIMEOUT = 1  # DNS 解析超时时间 秒
HOSTS_NUM = 2  # 限定最快 ip 数量
MAX_LATENCY = 300  # 允许的最大延迟
PING_TIMEOUT = 1  # ping 超时时间
NUM_PINGS = 4  # ping次数

# 初始化日志模块
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# 定义域名和IP组的数据类
class DomainGroup:
    def __init__(self, name: str, domains: List[str], ips: Set[str] = None):
        self.name = name
        self.domains = domains if isinstance(domains, list) else [domains]
        self.ips = ips if ips else set()


# 添加新的常量定义
INDIVIDUAL_GITHUB_URLS = {
    "github.com",
    "api.github.com",
    "gist.github.com",
    "raw.githubusercontent.com",
    "favicons.githubusercontent.com",
    "avatars5.githubusercontent.com",
    "avatars4.githubusercontent.com",
    "avatars3.githubusercontent.com",
    "avatars2.githubusercontent.com",
    "avatars1.githubusercontent.com",
    "avatars0.githubusercontent.com",
    "avatars.githubusercontent.com",
    "codeload.github.com",
    "github-releases.githubusercontent.com",
    "objects.githubusercontent.com",
}


# IPFetcher类用于从ipaddress.com获取IP
@dataclass
class IPResult:
    ips: Set[str]
    source: str
    latency: float


class ModernIPFetcher:
    def __init__(self, timeout: int = 5):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, IPResult] = {}

    async def __aenter__(self) -> "ModernIPFetcher":
        if not self._session:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; PythonIPFetcher/3.12)"
                },
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()
            self._session = None

    @lru_cache(maxsize=100)
    async def fetch_from_hellogithub(self) -> Optional[dict]:
        """从 HelloGithub 获取 IP 数据"""
        if not self._session:
            raise RuntimeError("Session not initialized")

        url = "https://raw.hellogithub.com/hosts.json"
        try:
            async with self._session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                warnings.warn(f"Failed to fetch from HelloGithub: {response.status}")
                return None
        except Exception as e:
            warnings.warn(f"Error fetching from HelloGithub: {e}")
            return None

    async def get_ips_for_domain(self, domain: str) -> Set[str]:
        """获取域名的 IP 地址集合"""
        if not self._session:
            raise RuntimeError("Session not initialized")

        results: Set[str] = set()

        # 尝试从 HelloGithub 获取
        hosts_data = await self.fetch_from_hellogithub()
        if hosts_data and domain in hosts_data:
            results.update(hosts_data[domain])

        # 尝试从 ipaddress.com 获取
        url = f"https://sites.ipaddress.com/{domain}"
        try:
            async with self._session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    # 同时匹配 IPv4 和 IPv6 地址
                    ipv4_pattern = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"
                    ipv6_pattern = r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"

                    results.update(re.findall(ipv4_pattern, text))
                    results.update(re.findall(ipv6_pattern, text))
        except Exception as e:
            warnings.warn(f"Error fetching from ipaddress.com: {e}")

        return results

    async def fetch_with_metrics(self, domain: str) -> IPResult:
        """获取域名解析结果并记录性能指标"""
        start_time = asyncio.get_event_loop().time()
        ips = await self.get_ips_for_domain(domain)
        latency = (asyncio.get_event_loop().time() - start_time) * 1000

        return IPResult(ips=ips, source="combined", latency=latency)


@dataclass
class DomainTestResult:
    """域名测试结果数据类"""

    domain: str
    ip: str
    latency: float
    is_ipv6: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class HostsEntry:
    """hosts 文件条目数据类"""

    ip: str
    domain: str
    group: str
    comment: str = ""


class ModernSetHosts:
    def __init__(
        self,
        domain_groups: List[DomainGroup],
        custom_dns_servers: Optional[List[str]] = None,
        max_workers: int = 4,
        hosts_num: int = 2,
        max_latency: int = 300,
    ):
        self.domain_groups = domain_groups
        self.dns_servers = custom_dns_servers or [
            "2402:4e00::",  # DNSPod (IPv6)
            "223.5.5.5",  # Alibaba DNS (IPv4)
            "119.29.29.29",  # DNSPod (IPv4)
            "2400:3200::1",  # Alibaba DNS (IPv6)
            "8.8.8.8",  # Google DNS (IPv4)
            "2001:4860:4860::8888",  # Google DNS (IPv6)
        ]
        self.hosts_num = hosts_num
        self.max_latency = max_latency
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.ip_fetcher: Optional[ModernIPFetcher] = None
        self._dns_cache: Dict[str, Set[str]] = {}

    @property
    def hosts_file_path(self) -> Path:
        """获取 hosts 文件路径"""
        if platform.system().lower() == "windows":
            return Path(r"C:\Windows\System32\drivers\etc\hosts")
        return Path("/etc/hosts")

    async def _create_dns_resolver(self, dns_server: str) -> dns.asyncresolver.Resolver:
        """创建 DNS 解析器"""
        resolver = dns.asyncresolver.Resolver()
        resolver.nameservers = [dns_server]
        resolver.lifetime = 1
        return resolver

    @lru_cache(maxsize=100)
    async def resolve_domain(self, domain: str) -> Set[str]:
        """解析域名获取 IP 地址"""
        ips = set()
        resolvers = [await self._create_dns_resolver(dns) for dns in self.dns_servers]

        async def try_resolve(resolver, qtype):
            try:
                answers = await resolver.resolve(domain, qtype)
                return {answer.address for answer in answers}
            except dns.exception.DNSException as e:
                logging.error(f"DNS解析失败: {domain}, 类型: {qtype}, 错误: {str(e)}")
                return set()

        tasks = [try_resolve(resolver, qtype) for resolver in resolvers for qtype in ["A", "AAAA"]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, set):
                ips.update(result)

        # 避免重复await
        if self.ip_fetcher and not getattr(self, '_ip_fetched', False):
            fetcher_ips = await self.ip_fetcher.get_ips_for_domain(domain)
            ips.update(fetcher_ips)
            self._ip_fetched = True

        if not ips:
            logging.warning(f"未找到有效IP: {domain}")
        return ips
    async def test_ip_connection(self, ip: str, port: int = 443) -> float:
        try:
            # 使用 getaddrinfo 来获取正确的地址格式
            addrinfo = await asyncio.get_event_loop().getaddrinfo(
                ip, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )

            for family, type, proto, canonname, sockaddr in addrinfo:
                try:
                    start = asyncio.get_event_loop().time()
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(sockaddr[0], sockaddr[1]),
                        timeout=PING_TIMEOUT,
                    )
                    end = asyncio.get_event_loop().time()
                    writer.close()
                    await writer.wait_closed()
                    return (end - start) * 1000
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logging.debug(f"连接测试失败 {ip} (sockaddr: {sockaddr}): {e}")
                    continue

            return float("inf")
        except Exception as e:
            logging.debug(f"获取地址信息失败 {ip}: {e}")
            return float("inf")


    async def test_connection(self, ip: str, port: int = 443) -> Tuple[bool, float]:
        """测试 IP 连接性能"""
        latency = await self.test_ip_connection(ip, port)
        success = latency != float("inf")
        return success, latency

    async def process_domain_group(self, group: DomainGroup) -> List[HostsEntry]:
        entries = []
        all_ips = group.ips.copy()

        # 解析所有域名的 IP
        resolve_tasks = []
        for domain in group.domains:
            if domain not in INDIVIDUAL_GITHUB_URLS:
                resolve_tasks.append(self.resolve_domain(domain))

        ip_sets = await asyncio.gather(*resolve_tasks)
        for ip_set in ip_sets:
            all_ips.update(ip_set)

        if not all_ips:
            warnings.warn(f"组 {group.name} 未找到任何可用IP")
            return entries

        # 测试所有 IP 的连接性能
        test_results: List[DomainTestResult] = []
        
        # 创建进度条
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True
        )
        
        # 开始进度条
        progress.start()
        task = progress.add_task(f"测试组 {group.name} 的IP连接性能...", total=len(all_ips))

        try:
            for ip in all_ips:
                success, latency = await self.test_connection(ip)
                if success and latency < self.max_latency:
                    test_results.append(
                        DomainTestResult(
                            domain=group.name, ip=ip, latency=latency, is_ipv6=":" in ip
                        )
                    )
                progress.update(task, advance=1)
        finally:
            # 确保进度条被停止
            progress.stop()

        if not test_results:
            warnings.warn(f"组 {group.name} 未找到延迟满足要求的IP")
            return entries

        # 选择最佳 IP（确保 IPv4 和 IPv6 都有代表）
        test_results.sort(key=lambda x: x.latency)
        selected_ips = []
        ipv4_count = ipv6_count = 0

        for result in test_results:
            if result.is_ipv6 and ipv6_count < self.hosts_num / 2:
                selected_ips.append(result)
                ipv6_count += 1
            elif not result.is_ipv6 and ipv4_count < self.hosts_num / 2:
                selected_ips.append(result)
                ipv4_count += 1

            if ipv4_count + ipv6_count >= self.hosts_num:
                break

        # 创建 hosts 条目
        for domain in group.domains:
            if domain not in INDIVIDUAL_GITHUB_URLS:
                for result in selected_ips:
                    entries.append(
                        HostsEntry(
                            ip=result.ip,
                            domain=domain,
                            group=group.name,
                            comment=f"latency: {result.latency:.2f}ms",
                        )
                    )

        return entries

    async def backup_hosts_file(self):
        """备份 hosts 文件"""
        if self.hosts_file_path.exists():
            backup_path = self.hosts_file_path.with_suffix(".bak")
            shutil.copy(str(self.hosts_file_path), str(backup_path))
            rprint(f"[bold blue]已备份 hosts 文件到 {backup_path}[/bold blue]")

    async def write_hosts_file(self, entries: List[HostsEntry]):
        """写入 hosts 文件"""
        update_time = datetime.now(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # 读取现有内容
        current_content = self.hosts_file_path.read_text().splitlines()
        new_content = []
        skip = False

        # 过滤掉旧的条目
        for line in current_content:
            if any(
                group.name in line and "Start" in line for group in self.domain_groups
            ):
                skip = True
                continue
            if any(
                group.name in line and "End" in line for group in self.domain_groups
            ):
                skip = False
                continue
            if not skip:
                new_content.append(line)

        # 按组组织新条目
        grouped_entries: Dict[str, List[HostsEntry]] = {}
        for entry in entries:
            if entry.group not in grouped_entries:
                grouped_entries[entry.group] = []
            grouped_entries[entry.group].append(entry)

        # 写入新条目
        for group_name, group_entries in grouped_entries.items():
            new_content.extend(
                [f"\n# {group_name} Start", f"# Update time: {update_time}"]
            )

            for entry in group_entries:
                new_content.append(
                    f"{entry.ip.ljust(30)} {entry.domain} # {entry.comment}"
                )

            new_content.append(f"# {group_name} End\n")

        # 写入文件
        self.hosts_file_path.write_text("\n".join(new_content))

    @asynccontextmanager
    async def get_ip_fetcher(self):
        """获取 IP Fetcher 的上下文管理器"""
        async with ModernIPFetcher() as fetcher:
            self.ip_fetcher = fetcher
            try:
                yield
            finally:
                self.ip_fetcher = None

    async def update_hosts(self):
        """更新 hosts 文件的主方法"""
        async with self.get_ip_fetcher():
            all_entries = []

            # 处理常规域名组
            for i, group in enumerate(self.domain_groups, 1):
                rprint(f"\n[bold]正在处理第 {i} 组 {group.name}...[/bold]")
                entries = await self.process_domain_group(group)
                all_entries.extend(entries)

            # 处理需要单独解析的域名
            if INDIVIDUAL_GITHUB_URLS:
                rprint("\n[bold]正在处理单独解析的GitHub域名...[/bold]")
                for domain in INDIVIDUAL_GITHUB_URLS:
                    ips = await self.resolve_domain(domain)
                    test_results = []

                    for ip in ips:
                        success, latency = await self.test_connection(ip)
                        if success and latency < self.max_latency:
                            test_results.append(
                                DomainTestResult(
                                    domain=domain,
                                    ip=ip,
                                    latency=latency,
                                    is_ipv6=":" in ip,
                                )
                            )

                    if test_results:
                        test_results.sort(key=lambda x: x.latency)
                        for result in test_results[: self.hosts_num]:
                            all_entries.append(
                                HostsEntry(
                                    ip=result.ip,
                                    domain=domain,
                                    group="Individual GitHub Domains",
                                    comment=f"latency: {result.latency:.2f}ms",
                                )
                            )
                            rprint(
                                f"域名 {domain} 使用IP: [green]{result.ip}[/green] "
                                f"(延迟: [yellow]{result.latency:.2f} ms[/yellow])"
                            )
                    else:
                        warnings.warn(f"域名 {domain} 未找到延迟满足要求的IP")

            # 更新 hosts 文件
            if all_entries:
                await self.backup_hosts_file()
                await self.write_hosts_file(all_entries)
                rprint("\n[bold green]hosts文件更新完成！[/bold green]")
            else:
                rprint(
                    "\n[bold red]警告: 没有有效条目可写入。hosts文件未更新。[/bold red]"
                )


# 域名组配置
DOMAIN_GROUPS = [
    DomainGroup(
        name="GitHub主站",
        domains=[
            "github.com",
        ],
        ips={
            "20.205.243.166",
            "20.27.177.113",
            "20.207.73.82",
            "20.233.83.145 ",
            "140.82.121.4 ",
        },
    ),
    DomainGroup(
        name="GitHub API",
        domains=[
            "api.github.com",
        ],
        ips={
            "20.205.243.168",
        },
    ),
    DomainGroup(
        name="GitHub Content and Assets",
        domains=[
            "github.githubassets.com",
            "central.github.com",
            "desktop.githubusercontent.com",
            "assets-cdn.github.com",
            "camo.githubusercontent.com",
            "github.map.fastly.net",
            "github.global.ssl.fastly.net",
            "live.github.com",
            "media.githubusercontent.com",
            "private-user-images.githubusercontent.com",
            "user-images.githubusercontent.com",
            "repository-images.githubusercontent.com",
            "marketplace-screenshots.githubusercontent.com",
            "githubstatus.com",
        ],
    ),
    DomainGroup(
        name="TMDB API",
        domains=[
            "tmdb.org",
            "api.tmdb.org",
        ],
        ips={},
    ),
    DomainGroup(
        name="themoviedb",
        domains=[
            "themoviedb.org",
            "api.themoviedb.org",
            "www.themoviedb.org",
            "auth.themoviedb.org",
        ],
        ips={},
    ),
    DomainGroup(
        name="TMDB 封面",
        domains=["image.tmdb.org", "images.tmdb.org"],
        ips={
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
        },
    ),
    DomainGroup(
        name="Google翻译",
        domains=[
            "translate.google.com",
            "translate.googleapis.com",
            "translate-pa.googleapis.com",
        ],
        ips={
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
            "74.125.204.139",
        },
    ),
    DomainGroup(
        name="Jetbrain",
        #
        domains=[
            "plugins.jetbrains.com",
            "download.jetbrains.com",
            "cache-redirector.jetbrains.com",
        ],
        ips={
            "133.33.5.36",
            "18.65.166.20",
            "52.84.251.69",
            "2a00:1450:4001:803::201a",
            "35.210.233.33",
            "74.125.204.139",
        },
    ),
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Hosts文件更新工具，请使用管理员权限运行"
    )
    parser.add_argument(
        "--log-level",
        "--log",
        "--l",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="设置日志输出等级",
    )
    parser.add_argument(
        "--hosts-num",
        "--num",
        "--n",
        default=HOSTS_NUM,
        type=int,
        help="设置选择的最快IP数量",
    )
    parser.add_argument(
        "--max-latency",
        "--max",
        "--tcp",
        default=MAX_LATENCY,
        type=int,
        help="设置允许的最大延迟（毫秒）",
    )
    return parser.parse_args()


args = parse_args()
logging.getLogger().setLevel(args.log_level.upper())


def main():
        # 设置事件循环为 SelectorEventLoop
    loop = SelectorEventLoop()
    asyncio.set_event_loop(loop)

    args = parse_args()
    logging.getLogger().setLevel(args.log_level.upper())

    rprint("[bold]启动Hosts文件更新器...[/bold]")
    rprint("[bold]初始化SetHosts ...[/bold]")

    hosts_updater = ModernSetHosts(
        domain_groups=DOMAIN_GROUPS, 
        custom_dns_servers=None,  # 使用默认DNS服务器
        hosts_num=args.hosts_num,
        max_latency=args.max_latency
    )

    rprint("[bold]开始更新hosts文件...[/bold]")
    try:
        # 使用 run_until_complete 来等待 update_hosts 协程
        loop.run_until_complete(hosts_updater.update_hosts())
    finally:
        loop.close()

    # 记录代码运行时间
    end_time = datetime.now()
    elapsed_time = end_time - start_time
    rprint(
        f"[bold]代码运行时间:[/bold] [cyan]{elapsed_time.total_seconds():.2f} 秒[/cyan]"
    )


def is_admin() -> bool:
    try:
        return os.getuid() == 0
    except AttributeError:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0


def run_as_admin():
    if is_admin():
        return

    if sys.platform.startswith("win"):
        script = os.path.abspath(sys.argv[0])
        params = " ".join([script] + sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
    else:
        os.execvp("sudo", ["sudo", "python3"] + sys.argv)
    sys.exit(0)


if __name__ == "__main__":
    if not is_admin():
        rprint(
            "[bold red]需要管理员权限来修改hosts文件。正在尝试提升权限...[/bold red]"
        )
        run_as_admin()

    main()  # 直接调用 main() 函数
