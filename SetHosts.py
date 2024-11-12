import os
import sys
from pathlib import Path
import dns.resolver
import json
import shutil
import asyncio
import platform
import logging
import argparse
import aiohttp
import socket
from enum import Enum
from datetime import datetime, timedelta, timezone
from typing import List, Set, Optional, Dict, Tuple
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint
import ctypes
import re
from functools import wraps

import wcwidth

# -------------------- 常量设置 -------------------- #
RESOLVER_TIMEOUT = 1  # DNS 解析超时时间 秒
HOSTS_NUM = 1  # 每个域名限定Hosts主机 ipv4 数量
MAX_LATENCY = 300  # 允许的最大延迟
PING_TIMEOUT = 1  # ping 超时时间
NUM_PINGS = 4  # ping次数

# 初始化日志模块
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# -------------------- 解析参数 -------------------- #
def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "------------------------------------------------------------\n"
            "Hosts文件更新工具,此工具可自动解析域名并优化系统的hosts文件\n"
            "------------------------------------------------------------\n"
        ),
        epilog=(
            "------------------------------------------------------------\n"
            "项目: https://github.com/sinspired/cnNetTool\n"
            "作者: Sinspired\n"
            "邮箱: ggmomo@gmail.com\n"
            "发布: 2024-11-11\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,  # 允许换行格式
    )

    parser.add_argument(
        "--log",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="设置日志输出等级",
    )
    parser.add_argument(
        "--hosts-num",
        "--num",
        default=HOSTS_NUM,
        type=int,
        help="限定Hosts主机 ip 数量",
    )
    parser.add_argument(
        "--max-latency",
        "--max",
        default=MAX_LATENCY,
        type=int,
        help="设置允许的最大延迟（毫秒）",
    )
    return parser.parse_args()


args = parse_args()
logging.getLogger().setLevel(args.log.upper())


# -------------------- 辅助功能模块 -------------------- #
class Utils:
    @staticmethod
    def is_ipv6(ip: str) -> bool:
        return ":" in ip

    @staticmethod
    def get_hosts_file_path() -> str:
        os_type = platform.system().lower()
        if os_type == "windows":
            return r"C:\Windows\System32\drivers\etc\hosts"
        elif os_type in ["linux", "darwin"]:
            return "/etc/hosts"
        else:
            raise ValueError("不支持的操作系统")

    @staticmethod
    def backup_hosts_file(hosts_file_path: str):
        if os.path.exists(hosts_file_path):
            backup_path = f"{hosts_file_path}.bak"
            shutil.copy(hosts_file_path, backup_path)
            rprint(
                f"\n[blue]已备份 [underline]{hosts_file_path}[/underline] 到 [underline]{backup_path}[/underline][/blue]"
            )

    def get_align_str(
        i,
        group_name,
        reference_str="启动 setHosts 自动更新···                                 ",
    ):
        """
        创建一个经过填充的进度字符串，使其显示宽度与参考字符串相同

        Args:
            i: 当前处理的组索引
            group_name: 组名称
            reference_str: 参考字符串，用于对齐长度

        Returns:
            调整后的格式化字符串
        """
        # 计算参考字符串的显示宽度
        ref_width = wcwidth.wcswidth(reference_str)

        # 构建基础字符串（不包含尾部填充）
        base_str = f"正在处理第 {i} 组域名： {group_name}"

        # 计算基础字符串的显示宽度
        base_width = wcwidth.wcswidth(base_str)

        # 计算需要添加的空格数量
        # 需要考虑Rich标签不计入显示宽度
        padding_needed = ref_width - base_width

        # 确保填充不会为负数
        padding_needed = max(0, padding_needed)

        # 构建最终的格式化字符串
        formatted_str = f"\n[bold white on bright_black]正在处理第 [green]{i}[/green] 组域名： {group_name}{' ' * padding_needed}[/bold white on bright_black]"

        return formatted_str


# -------------------- 域名与分组管理 -------------------- #
class GroupType(Enum):
    SHARED = "shared hosts"  # 多个域名共用一组DNS主机 IP
    SEPARATE = "separate hosts"  # 每个域名独立拥有DNS主机 IP


class DomainGroup:
    def __init__(
        self,
        name: str,
        domains: List[str],
        ips: Optional[Set[str]] = None,
        group_type: GroupType = GroupType.SHARED,
    ):
        self.name = name
        self.domains = domains if isinstance(domains, list) else [domains]
        self.ips = ips or set()
        self.group_type = group_type


# -------------------- 域名解析模块 -------------------- #
class DomainResolver:
    # 设置缓存过期时间为1周
    DNS_CACHE_EXPIRY_TIME = timedelta(weeks=1)

    def __init__(self, dns_servers: List[str], max_latency: int, dns_cache_file: str):
        self.dns_servers = dns_servers
        self.max_latency = max_latency
        self.dns_cache_file = Path(dns_cache_file)
        self.dns_records = self._init_dns_cache()

    def _init_dns_cache(self) -> dict:
        """初始化 DNS 缓存，如果缓存文件存在且未过期则加载，否则返回空字典"""
        if self._is_dns_cache_valid():
            return self.load_hosts_cache()
        # 如果 DNS 缓存过期，删除旧缓存文件
        if self.dns_cache_file.exists():
            self.dns_cache_file.unlink()
        return {}

    def _is_dns_cache_valid(self) -> bool:
        """检查 DNS 缓存是否有效"""
        if not self.dns_cache_file.exists():
            return False

        file_age = datetime.now() - datetime.fromtimestamp(
            os.path.getmtime(self.dns_cache_file)
        )
        return file_age <= self.DNS_CACHE_EXPIRY_TIME

    def load_hosts_cache(self) -> Dict[str, Dict]:
        try:
            with open(self.dns_cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"加载 DNS 缓存文件失败: {e}")
            return {}

    def save_hosts_cache(self):
        try:
            with open(self.dns_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.dns_records, f, indent=4, ensure_ascii=False)
            logging.debug(f"成功保存 DNS 缓存到文件 {self.dns_cache_file}")
        except Exception as e:
            logging.error(f"保存 DNS 缓存到文件时发生错误: {e}")

    async def resolve_domain(self, domain: str) -> Set[str]:
        ips = set()

        # 1. 首先通过常规DNS服务器解析
        # dns_ips = await self._resolve_via_dns(domain)
        # ips.update(dns_ips)

        # 2. 然后通过DNS_records解析
        # 由于init时已经处理了过期文件，这里只需要检查域名是否在缓存中
        if domain in self.dns_records:
            domain_hosts = self.dns_records.get(domain, {})
            ipv4_ips = domain_hosts.get("ipv4", [])
            ipv6_ips = domain_hosts.get("ipv6", [])

            ips.update(ipv4_ips + ipv6_ips)
        else:
            ipaddress_ips = await self._resolve_via_ipaddress(domain)
            ips.update(ipaddress_ips)

        if ips:
            logging.debug(f"成功解析 {domain}, 找到 {len(ips)} 个 DNS 主机")
        else:
            logging.debug(f"警告: 无法解析 {domain}")

        return ips

    async def _resolve_via_dns(self, domain: str) -> Set[str]:
        ips = set()
        for dns_server in self.dns_servers:
            try:
                resolver = dns.resolver.Resolver()
                resolver.nameservers = [dns_server]
                resolver.lifetime = RESOLVER_TIMEOUT

                for qtype in ["A", "AAAA"]:
                    try:
                        answers = await asyncio.to_thread(
                            resolver.resolve, domain, qtype
                        )
                        ips.update(answer.address for answer in answers)
                    except dns.resolver.NoAnswer:
                        pass

                if ips:
                    logging.debug(f"成功使用 {dns_server} 解析 {domain}")
                    logging.debug(f"DNS_resolver：\n {ips}")
                    return ips
            except Exception as e:
                logging.debug(f"使用 {dns_server} 解析 {domain} 失败: {e}")

        return ips

    def retry_async(tries=3, delay=0):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                for attempt in range(tries):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        if attempt == tries - 1:
                            raise e
                        await asyncio.sleep(delay)
                return None

            return wrapper

        return decorator

    @retry_async(tries=3)
    async def _resolve_via_ipaddress(self, domain: str) -> Set[str]:
        ips = set()
        url = f"https://sites.ipaddress.com/{domain}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/106.0.0.0 Safari/537.36"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=5) as response:
                    if response.status != 200:
                        logging.info(
                            f"DNS_records(ipaddress.com) 查询请求失败: {response.status}"
                        )
                        return ips

                    content = await response.text()
                    # 匹配IPv4地址
                    ipv4_pattern = r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"
                    ipv4_ips = set(re.findall(ipv4_pattern, content))

                    # 匹配IPv6地址
                    ipv6_pattern = r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
                    ipv6_ips = set(re.findall(ipv6_pattern, content))

                    ips.update(ipv4_ips)
                    ips.update(ipv6_ips)

                    if ips:
                        # 更新hosts缓存
                        current_time = datetime.now().isoformat()
                        self.dns_records[domain] = {
                            "last_update": current_time,
                            "ipv4": list(ipv4_ips),
                            "ipv6": list(ipv6_ips),
                            "source": "DNS_records",
                        }
                        # 保存到文件
                        self.save_hosts_cache()
                        logging.debug(
                            f"通过 ipaddress.com 成功解析 {domain} 并更新 DNS_records 缓存"
                        )
                        logging.debug(f"DNS_records：\n {ips}")
                    else:
                        logging.warning(
                            f"ipaddress.com 未找到 {domain} 的 DNS_records 地址"
                        )

        except Exception as e:
            logging.error(f"通过DNS_records解析 {domain} 失败: {e}")

        return ips


# -------------------- 延迟测速模块 -------------------- #


class LatencyTester:
    def __init__(self, resolver: DomainResolver, hosts_num: int):
        self.resolver = resolver
        self.hosts_num = hosts_num

    async def get_latency(self, ip: str, port: int = 443) -> float:
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
            logging.error(f"获取地址信息失败 {ip}: {e}")
            return float("inf")

    async def get_host_average_latency(
        self, ip: str, port: int = 443
    ) -> Tuple[str, float]:
        try:
            response_times = await asyncio.gather(
                *[self.get_latency(ip, port) for _ in range(NUM_PINGS)]
            )
            response_times = [t for t in response_times if t != float("inf")]
            if response_times:
                average_response_time = sum(response_times) / len(response_times)
            else:
                average_response_time = float("inf")

            if average_response_time == 0:
                logging.error(f"{ip} 平均延迟为 0 ms，视为无效")
                return ip, float("inf")

            logging.debug(f"{ip} 平均延迟: {average_response_time:.2f} ms")
            return ip, average_response_time
        except Exception as e:
            logging.debug(f"ping {ip} 时出错: {e}")
            return ip, float("inf")

    async def get_lowest_latency_hosts(
        self, domains: List[str], file_ips: Set[str], latency_limit: int
    ) -> List[Tuple[str, float]]:
        all_ips = set()

        if args.log.upper() == "INFO":
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}\n"),
                transient=True,
            ) as progress:
                task = progress.add_task("正在解析域名...", total=len(domains))
                tasks = [self.resolver.resolve_domain(domain) for domain in domains]

                for ips in await asyncio.gather(*tasks):
                    all_ips.update(ips)
                    progress.update(task, advance=1)
                all_ips.update(file_ips)
        else:
            tasks = [self.resolver.resolve_domain(domain) for domain in domains]
            for ips in await asyncio.gather(*tasks):
                all_ips.update(ips)
            all_ips.update(file_ips)

        rprint(
            f"[bright_black]- 找到 [bold bright_green]{len(all_ips)}[/bold bright_green] 个唯一IP地址[/bright_black]"
        )

        if args.log.upper() == "INFO":
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("正在 ping 所有IP地址...", total=len(all_ips))
                ping_tasks = [self.get_host_average_latency(ip) for ip in all_ips]
                results = []
                for result in await asyncio.gather(*ping_tasks):
                    results.append(result)
                    progress.update(task, advance=1)
        else:
            ping_tasks = [self.get_host_average_latency(ip) for ip in all_ips]
            results = []
            for result in await asyncio.gather(*ping_tasks):
                results.append(result)

        valid_results = [result for result in results if result[1] < latency_limit]

        if not valid_results:
            logging.warning(f"未找到延迟小于 {latency_limit}ms 的IP。")
            if results:
                latency_limit = latency_limit * 2
                logging.info(f"放宽延迟限制为 {latency_limit}ms 重新搜索...")
                valid_results = [
                    result for result in results if result[1] < latency_limit
                ]
            if not valid_results:
                return []

        ipv4_results = [r for r in valid_results if not Utils.is_ipv6(r[0])]
        ipv6_results = [r for r in valid_results if Utils.is_ipv6(r[0])]

        best_hosts = []
        if ipv4_results and ipv6_results:
            best_hosts.append(min(ipv4_results, key=lambda x: x[1]))
            best_hosts.append(min(ipv6_results, key=lambda x: x[1]))
        else:
            best_hosts = sorted(valid_results, key=lambda x: x[1])[: self.hosts_num]

        rprint(
            f"[bold yellow]最快的 DNS主机 IP（优先选择 IPv6） 丨   延迟 < {latency_limit}ms ：[/bold yellow]"
        )
        for ip, time in best_hosts:
            rprint(
                f"  [green]{ip}[/green]    [bright_black]{time:.2f} ms[/bright_black]"
            )
        return best_hosts


# -------------------- Hosts文件管理 -------------------- #
class HostsManager:
    def __init__(self, resolver: DomainResolver):
        # 自动根据操作系统获取hosts文件路径
        self.hosts_file_path = self._get_hosts_file_path()
        self.resolver = resolver

    @staticmethod
    def _get_hosts_file_path() -> str:
        """根据操作系统自动获取 hosts 文件路径。"""
        return Utils.get_hosts_file_path()

    def write_to_hosts_file(self, new_entries: List[str]):
        Utils.backup_hosts_file(self.hosts_file_path)

        with open(self.hosts_file_path, "r") as f:
            existing_content = f.read().splitlines()

        new_domains = {
            entry.split()[1] for entry in new_entries if len(entry.split()) >= 2
        }

        new_content = []
        skip = False
        skip_tags = ("# cnNetTool-SetHosts", "# Update", "# Star", "# GitHub")

        for line in existing_content:
            line = line.strip()

            # 跳过标记块
            if any(line.startswith(tag) for tag in skip_tags):
                skip = True

            if line == "":
                skip = True

            if skip:
                if line == "" or line.startswith("#"):
                    continue
                skip = False

            # 非标记块内容保留
            if (
                not skip
                and (line.startswith("#") or not line)
                and not any(tag in line for tag in skip_tags)
            ):
                new_content.append(line)
                continue

            # 检查域名是否为新条目
            parts = line.split()
            if len(parts) >= 2 and parts[1] not in new_domains:
                new_content.append(line)
            else:
                logging.debug(f"删除旧条目: {line}")

        update_time = (
            datetime.now(timezone.utc)
            .astimezone(timezone(timedelta(hours=8)))
            .strftime("%Y-%m-%d %H:%M:%S %z")
            .replace("+0800", "+08:00")
        )

        rprint("\n[bold yellow]正在更新hosts文件...[/bold yellow]")

        # 1. 添加标题
        new_content.append("\n# cnNetTool-SetHosts 脚本 Start\n")

        # 2. 添加主机条目
        for entry in new_entries:
            # 分割 IP 和域名
            ip, domain = entry.strip().split(maxsplit=1)

            # 计算需要的制表符数量
            # IP 地址最长可能是 39 个字符 (IPv6)
            # 我们使用制表符(8个空格)来对齐，确保视觉上的整齐
            ip_length = len(ip)
            if ip_length <= 8:
                tabs = "\t\t\t"  # 两个制表符
            if ip_length <= 10:
                tabs = "\t\t"  # 两个制表符
            elif ip_length <= 16:
                tabs = "\t"  # 一个制表符
            else:
                tabs = "\t"  # 对于很长的IP，只使用一个空格

            # 返回格式化后的条目
            formatedEntry = f"{ip}{tabs}{domain}"
            new_content.append(formatedEntry)
            rprint(f"+ {formatedEntry}")

        # 3. 添加项目描述
        new_content.extend(
            [
                f"\n# Update time: {update_time}",
                "# GitHub仓库: https://github.com/sinspired/cnNetTool",
                "# cnNetTool-SetHosts 脚本 End\n",
            ]
        )

        # 4. 写入hosts文件
        with open(self.hosts_file_path, "w") as f:
            f.write("\n".join(new_content))


# -------------------- 主控制模块 -------------------- #
class HostsUpdater:
    def __init__(
        self,
        domain_groups: List[DomainGroup],
        resolver: DomainResolver,
        tester: LatencyTester,
        hosts_manager: HostsManager,
    ):
        self.domain_groups = domain_groups
        self.resolver = resolver
        self.tester = tester
        self.hosts_manager = hosts_manager

    async def update_hosts(self):
        # 更新hosts文件的主逻辑
        all_entries = []
        for i, group in enumerate(self.domain_groups, 1):
            progress_str = Utils.get_align_str(i, group.name)
            rprint(progress_str)

            all_ips = group.ips.copy()  # 从预设IP开始

            # 2. 根据不同组设置IP
            if group.group_type == GroupType.SEPARATE:
                for domain in group.domains:
                    rprint(f"\n为域名 {domain} 设置 DNS 映射主机")
                    all_ips = set()
                    resolved_ips = await self.resolver.resolve_domain(domain)
                    all_ips.update(resolved_ips)
                    if not all_ips:
                        logging.warning(f"{domain} 未找到任何可用IP。跳过该域名。")
                        continue

                    fastest_ips = await self.tester.get_lowest_latency_hosts(
                        [domain],
                        all_ips,
                        self.resolver.max_latency,
                    )
                    if not fastest_ips:
                        logging.warning(f"{domain} 未找到延迟满足要求的IP。")
                        continue

                    new_entries = [f"{ip}\t{domain}" for ip, latency in fastest_ips]
                    all_entries.extend(new_entries)
            else:
                # 收集组内所有域名的DNS解析结果
                for domain in group.domains:
                    resolved_ips = await self.resolver.resolve_domain(domain)
                    all_ips.update(resolved_ips)

                if not all_ips:
                    logging.warning(f"组 {group.name} 未找到任何可用IP。跳过该组。")
                    continue

                rprint(f"  找到 {len(all_ips)} 个 DNS 主机记录")

                fastest_ips = await self.tester.get_lowest_latency_hosts(
                    # [group.domains[0]],  # 只需传入一个域名，因为只是用来测试IP
                    group.domains,  # 传入所有域名以获得更准确的延迟测试结果
                    all_ips,
                    self.resolver.max_latency,
                )

                if not fastest_ips:
                    logging.warning(f"组 {group.name} 未找到延迟满足要求的IP。")
                    continue

                rprint(
                    f"\n[bold]为组 {group.name} 内所有域名应用延迟最低的 DNS 映射主机IP:[/bold]"
                )
                for domain in group.domains:
                    new_entries = [f"{ip}\t{domain}" for ip, latency in fastest_ips]
                    rprint(f"[bright_black]  - {domain}[/bright_black]")
                    all_entries.extend(new_entries)

        if all_entries:
            self.hosts_manager.write_to_hosts_file(all_entries)
            rprint(
                "\n[blue on green]Hosts 文件更新......................................... [完成][/blue on green]"
            )
        else:
            rprint("\n[bold red]警告: 没有有效条目可写入。hosts文件未更新。[/bold red]")


# -------------------- 权限提升模块-------------------- #
class PrivilegeManager:
    @staticmethod
    def is_admin() -> bool:
        try:
            return os.getuid() == 0
        except AttributeError:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0

    @staticmethod
    def run_as_admin():
        if PrivilegeManager.is_admin():
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


# -------------------- 数据配置模块-------------------- #


class Config:
    DOMAIN_GROUPS = [
        DomainGroup(
            name="GitHub",
            group_type=GroupType.SEPARATE,
            domains=[
                "github.com",
                "api.github.com",
                "gist.github.com",
                "alive.github.com",
                "github.community",
                "central.github.com",
                "codeload.github.com",
                "collector.github.com",
                "vscode.dev",
                "github.blog",
                "live.github.com",
                "education.github.com",
                "github.global.ssl.fastly.net",
                "pipelines.actions.githubusercontent.com",
                "github-com.s3.amazonaws.com",
                "github-cloud.s3.amazonaws.com",
                "github-production-user-asset-6210df.s3.amazonaws.com",
                "github-production-release-asset-2e65be.s3.amazonaws.com",
                "github-production-repository-file-5c1aeb.s3.amazonaws.com",
            ],
            ips={},
        ),
        DomainGroup(
            name="GitHub Shared 01",
            group_type=GroupType.SHARED,
            domains=[
                "github.io",
                "githubstatus.com",
                "assets-cdn.github.com",
                "github.githubassets.com",
            ],
            ips={},
        ),
        DomainGroup(
            name="GitHub Shared 02",
            group_type=GroupType.SHARED,
            domains=[
                "avatars.githubusercontent.com",
                "avatars0.githubusercontent.com",
                "avatars1.githubusercontent.com",
                "avatars2.githubusercontent.com",
                "avatars3.githubusercontent.com",
                "avatars4.githubusercontent.com",
                "avatars5.githubusercontent.com",
                "camo.githubusercontent.com",
                "cloud.githubusercontent.com",
                "desktop.githubusercontent.com",
                "favicons.githubusercontent.com",
                "github.map.fastly.net",
                "raw.githubusercontent.com",
                "media.githubusercontent.com",
                "objects.githubusercontent.com",
                "user-images.githubusercontent.com",
                "private-user-images.githubusercontent.com",
            ],
            ips={},
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
            name="THE MOVIEDB",
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
            ips={},
        ),
        DomainGroup(
            name="Google 翻译",
            domains=[
                "translate.google.com",
                "translate.googleapis.com",
                "translate-pa.googleapis.com",
            ],
            ips={
                "216.239.32.40",
                "2404:6800:4008:c15::94",
                "2a00:1450:4001:829::201a",
                "2404:6800:4008:c13::5a",
                "2a00:1450:4001:803::201a",
                "74.125.204.139",
                "2607:f8b0:4004:c07::66",
                "2607:f8b0:4004:c07::71",
                "2607:f8b0:4004:c07::8a",
                "2607:f8b0:4004:c07::8b",
                "172.253.62.100",
                "172.253.62.101",
                "172.253.62.102",
                "172.253.62.103",
                "35.197.239.137",
                "35.189.113.240",
                "35.228.168.221",
                "35.210.233.33",
            },
        ),
        DomainGroup(
            name="JetBrain 插件下载",
            domains=[
                "plugins.jetbrains.com",
                "download.jetbrains.com",
                "cache-redirector.jetbrains.com",
            ],
            ips={},
        ),
    ]

    # DNS 服务器
    DNS_SERVERS = [
        "2402:4e00::",  # DNSPod (IPv6)
        "223.5.5.5",  # Alibaba DNS (IPv4)
        "119.29.29.29",  # DNSPod (IPv4)
        "2400:3200::1",  # Alibaba DNS (IPv6)
        "8.8.8.8",  # Google Public DNS (IPv4)
        "2001:4860:4860::8888",  # Google Public DNS (IPv6)
        "114.114.114.114",  # 114 DNS
        "208.67.222.222",  # Open DNS (IPv4)
        "2620:0:ccc::2",  # Open DNS (IPv6)
    ]

    @staticmethod
    def get_dns_cache_file() -> Path:
        """获取 DNS 缓存文件路径，并确保目录存在。"""
        current_dir = Path(__file__).resolve().parent  # 获取当前脚本所在的目录
        dns_cache_dir = current_dir / "dns_cache"
        dns_cache_dir.mkdir(exist_ok=True)  # 确保缓存目录存在

        # (提示：dns_records.json 文件将存储 A、AAAA 等 DNS 资源记录缓存。)
        return dns_cache_dir / "dns_records.json"  # 返回缓存文件路径


# -------------------- 主函数入口 -------------------- #
async def main():
    rprint("[green]----------------------------------------------------------[/green]")
    rprint(
        "[blue on green]启动 setHosts 自动更新···                                 [/blue on green]"
    )
    rprint(
        "[green]----------------------------------------------------------[/green]\n"
    )

    start_time = datetime.now()  # 记录程序开始运行时间

    # 从配置类中加载DOMAIN_GROUPS、DNS_SERVERS和dns_cache_dir
    DOMAIN_GROUPS = Config.DOMAIN_GROUPS
    dns_servers = Config.DNS_SERVERS
    dns_cache_file = Config.get_dns_cache_file()

    # 1.域名解析
    resolver = DomainResolver(
        dns_servers=dns_servers,
        max_latency=args.max_latency,
        dns_cache_file=dns_cache_file,
    )

    # 2.延迟检测
    tester = LatencyTester(resolver=resolver, hosts_num=args.hosts_num)

    # 3.Hosts文件操作
    hosts_manager = HostsManager(resolver=resolver)

    # 4.初始化 Hosts更新器 参数
    updater = HostsUpdater(
        domain_groups=DOMAIN_GROUPS,
        resolver=resolver,
        tester=tester,
        hosts_manager=hosts_manager,
    )

    if not PrivilegeManager.is_admin():
        rprint(
            "[bold red]需要管理员权限来修改hosts文件。正在尝试提升权限...[/bold red]"
        )
        PrivilegeManager.run_as_admin()

    # 启动 Hosts更新器
    await updater.update_hosts()

    # 计算程序运行时间
    end_time = datetime.now()
    total_time = end_time - start_time
    rprint(
        f"[bold]代码运行时间:[/bold] [cyan]{total_time.total_seconds():.2f} 秒[/cyan]"
    )
    input("\n任务执行完毕，按任意键退出！")


if __name__ == "__main__":
    asyncio.run(main())
