import argparse
import asyncio
import ctypes
import json
import logging
import os
import platform
import re
import shutil
import socket
import ssl
import sys
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import wraps
from math import floor
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
import dns.resolver
from rich import print as rprint
from rich.progress import BarColumn, Progress, TaskID, TimeRemainingColumn

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
        "-log",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="设置日志输出等级",
    )
    parser.add_argument(
        "-num",
        "--hosts-num",
        default=HOSTS_NUM,
        type=int,
        help="限定Hosts主机 ip 数量",
    )
    parser.add_argument(
        "-max",
        "--max-latency",
        default=MAX_LATENCY,
        type=int,
        help="设置允许的最大延迟（毫秒）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="打印运行信息",
    )
    parser.add_argument(
        "-n",
        "--NotUseDnsServers",
        action="store_true",
        help="不使用DNS服务器解析（避免GitHub等被dns污染的网站获取错误地址)",
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

    @staticmethod
    def write_readme_file(
        hosts_content: List[str], temp_file_path: str, update_time: str
    ):
        """
        根据模板文件生成 README.md 文件,并将 hosts 文件内容写入其中。

        参数:
        hosts_content (List[str]): hosts 文件的内容,以列表形式传入
        temp_file_path (str): 输出的 README.md 文件路径
        update_time (str): hosts 文件的更新时间,格式为 "YYYY-MM-DD HH:MM:SS +0800"
        """
        try:
            # 获取template文件的绝对路径
            current_dir = os.path.dirname(os.path.abspath(__file__))
            template_path = os.path.join(current_dir, temp_file_path)

            if not os.path.exists(template_path):
                raise FileNotFoundError(f"模板文件未找到: {template_path}")

            # 读取模板文件
            with open(template_path, "r", encoding="utf-8") as temp_fb:
                template_content = temp_fb.read()

            # 将hosts内容转换为字符串
            hosts_str = "\n".join(hosts_content)

            # 使用替换方法而不是format
            readme_content = template_content.replace("{hosts_str}", hosts_str)
            readme_content = readme_content.replace("{update_time}", update_time)

            # 写入新文件
            with open("README.md", "w", encoding="utf-8") as output_fb:
                output_fb.write(readme_content)

            rprint(
                "[blue]已更新 README.md 文件,位于: [underline]README.md[/underline][/blue]\n"
            )

        except FileNotFoundError as e:
            print(f"错误: {str(e)}")
        except Exception as e:
            print(f"生成 README.md 文件时发生错误: {str(e)}")

    def get_formatted_line(char="-", color="green", width_percentage=0.97):
        """
        生成格式化的分隔线

        参数:
            char: 要重复的字符
            color: rich支持的颜色名称
            width_percentage: 终端宽度的百分比（0.0-1.0）
        """
        # 获取终端宽度
        terminal_width = shutil.get_terminal_size().columns
        # 计算目标宽度（终端宽度的指定百分比）
        target_width = floor(terminal_width * width_percentage)

        # 生成重复字符
        line = char * target_width

        # 返回带颜色标记的行
        return f"[{color}]{line}[/{color}]"

    def get_formatted_output(text, fill_char=".", align_position=0.97):
        """
        格式化输出文本，确保不超出终端宽度

        参数:
            text: 要格式化的文本
            fill_char: 填充字符
            align_position: 终端宽度的百分比（0.0-1.0）
        """
        # 获取终端宽度并计算目标宽度
        terminal_width = shutil.get_terminal_size().columns
        target_width = floor(terminal_width * align_position)

        # 移除rich标记计算实际文本长度
        plain_text = (
            text.replace("[blue on green]", "").replace("[/blue on green]", "")
            # .replace("[完成]", "")
        )

        if "[完成]" in text:
            main_text = plain_text.strip()
            completion_mark = "[完成]"
            # 关键修改：直接从目标宽度减去主文本长度，不再额外预留[完成]的空间
            fill_count = target_width - len(main_text) - len(completion_mark) - 6
            fill_count = max(0, fill_count)

            filled_text = f"{main_text}{fill_char * fill_count}{completion_mark}"
            return f"[blue on green]{filled_text}[/blue on green]"
        else:
            # 普通文本的处理保持不变
            fill_count = target_width - len(plain_text.strip()) - 6
            fill_count = max(0, fill_count)
            filled_text = f"{plain_text.strip()}{' ' * fill_count}"
            return f"[blue on green]{filled_text}[/blue on green]"


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
        if not args.NotUseDnsServers:
            dns_ips = await self._resolve_via_dns(domain)
            ips.update(dns_ips)

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
            logging.debug(f"成功解析 {domain}, 发现 {len(ips)} 个 DNS 主机")
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

    def retry_async(tries=3, delay=1):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                for attempt in range(tries):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        if attempt < tries - 1:
                            logging.debug(
                                f"通过DNS_records解析 {args[1]},第 {attempt + 2} 次尝试:"
                            )
                        if attempt == tries - 1:
                            logging.debug(
                                f"通过DNS_records解析 {args[1]},{tries} 次尝试后终止！"
                            )
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
                        logging.debug(
                            f"ipaddress.com 未解析到 {domain} 的 DNS_records 地址"
                        )

        except Exception as e:
            logging.error(f"通过DNS_records解析 {domain} 失败: {e}")
            raise

        return ips


# -------------------- 延迟测速模块 -------------------- #


class LatencyTester:
    def __init__(self, hosts_num: int):
        self.hosts_num = hosts_num
        self.progress = None
        self.current_task = None

    def set_progress(self, progress, task):
        """设置进度显示器和当前任务"""
        self.progress = progress
        self.current_task = task

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

            if self.progress and self.current_task:
                self.progress.update(self.current_task, advance=1)

            logging.debug(f"{ip} 平均延迟: {average_response_time:.2f} ms")
            return ip, average_response_time
        except Exception as e:
            logging.debug(f"ping {ip} 时出错: {e}")
            return ip, float("inf")

    async def is_cert_valid(self, domain: str, ip: str, port: int = 443) -> bool:

        # 设置SSL上下文，用于证书验证
        context = ssl.create_default_context()
        context.verify_mode = ssl.CERT_REQUIRED  # 验证服务器证书
        context.check_hostname = True  # 确保证书主机名匹配

        try:
            # 1. 尝试与IP地址建立SSL连接
            with socket.create_connection((ip, port), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
                    # 检查证书的有效期
                    not_after = datetime.strptime(
                        cert["notAfter"], "%b %d %H:%M:%S %Y %Z"
                    )
                    if not_after < datetime.now():
                        logging.debug(f"{domain} ({ip}): 证书已过期")
                        return False

                    # 验证证书域名（由context自动完成），同时获取连接状态
                    logging.debug(
                        f"{domain} ({ip}): SSL证书有效，截止日期为 {not_after}"
                    )
                    return True

        except ssl.SSLError as e:
            logging.debug(f"{domain} ({ip}): SSL错误 - {e}")
            return False
        except socket.timeout as e:
            logging.debug(f"{domain} ({ip}): 连接超时 - {e}")
            return False
        except ConnectionError as e:
            logging.debug(f"{domain} ({ip}): 连接被强迫关闭，ip有效 - {e}")
            return True
        except Exception as e:
            logging.error(f"{domain} ({ip}): 其他错误 - {e}")
            return False

    async def get_lowest_latency_hosts(
        self,
        group_name: str,
        domains: List[str],
        file_ips: Set[str],
        latency_limit: int,
        latency_task_id: TaskID,
    ) -> List[Tuple[str, float]]:
        all_ips = file_ips
        total_ips = len(all_ips)

        # 更新进度条描述和总数
        if self.progress and latency_task_id:
            self.progress.update(
                latency_task_id,
                total=total_ips,
                visible=False,
            )
        if args.verbose:
            rprint(
                f"[bright_black]- [{group_name}] {domains[0] if len(domains) == 1 else f'{len(domains)} 域名'} 解析到 [bold bright_green]{len(all_ips):2}[/bold bright_green] 个唯一IP地址 [{group_name}][/bright_black]"
            )

        # Ping所有IP
        ping_tasks = [self.get_host_average_latency(ip) for ip in all_ips]

        results = []
        # 使用 asyncio.as_completed 确保每个任务完成时立即处理
        for coro in asyncio.as_completed(ping_tasks):
            result = await coro
            results.append(result)

            # 每完成一个任务立即更新进度
            if self.progress and latency_task_id:
                self.progress.update(
                    latency_task_id,
                    advance=1,
                    visible=True,
                    total=total_ips,
                )

        if self.progress and latency_task_id:
            # 确保进度完结
            self.progress.update(
                latency_task_id,
                completed=total_ips,
                visible=True,
            )

        results = [result for result in results if result[1] != float("inf")]
        valid_results = []

        if results:
            valid_results = [result for result in results if result[1] < latency_limit]
            if not valid_results:
                logging.debug(
                    f"{group_name} {domains[0] if len(domains) == 1 else f'{len(domains)} 域名'} 未发现延迟小于 {latency_limit}ms 的IP。"
                )

                valid_results = [min(results, key=lambda x: x[1])]
                latency_limit = valid_results[0][1]
                logging.debug(
                    f"{group_name} {domains[0] if len(domains) == 1 else f'{len(domains)} 域名'} 的主机IP最低延迟{latency_limit}ms"
                )

        else:
            rprint(
                f"[red]{group_name} {domains[0] if len(domains) == 1 else f'{len(domains)} 域名'} 延迟检测没有获得有效IP[/red]"
            )
            return []

        # 排序结果
        valid_results = sorted(valid_results, key=lambda x: x[1])

        ipv4_results = [r for r in valid_results if not Utils.is_ipv6(r[0])]
        ipv6_results = [r for r in valid_results if Utils.is_ipv6(r[0])]

        best_hosts = []
        selected_count = 0

        # 检测 IPv4 证书有效性
        for ip, latency in ipv4_results:
            if await self.is_cert_valid(
                domains[0], ip
            ):  # shareGroup会传入多个域名，只需检测第一个就行
                best_hosts.append((ip, latency))
                selected_count += 1
                if ipv6_results or selected_count >= self.hosts_num:
                    break

        # 检测 IPv6 证书有效性
        if ipv6_results:
            for ip, latency in ipv6_results:
                if await self.is_cert_valid(domains[0], ip):
                    best_hosts.append((ip, latency))
                    break

        if args.verbose:
            rprint(
                f"[bold yellow]最快DNS主机 {'(IPv4/IPv6)' if ipv6_results else '(IPv4 Only)'} 延迟 < {latency_limit:.0f}ms | [{group_name}] "
                f"{domains[0] if len(domains) == 1 else f'{len(domains)} 域名合用 IP'}:[/bold yellow]"
            )

            for ip, time in best_hosts:
                rprint(
                    f"  [green]{ip}[/green]    [bright_black]{time:.2f} ms[/bright_black]"
                )

        return best_hosts


# -------------------- Hosts文件管理 -------------------- #
class HostsManager:
    def __init__(self):
        # 自动根据操作系统获取hosts文件路径
        self.hosts_file_path = self._get_hosts_file_path()

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
        skip_tags = ("# cnNetTool", "# Update", "# Star", "# GitHub")

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

        rprint("\n[bold yellow]正在更新 hosts 文件...[/bold yellow]")

        save_hosts_content = []  # 提取新内容文本

        # 1. 添加标题
        new_content.append(f"\n# cnNetTool Start in {update_time}")
        save_hosts_content.append(f"\n# cnNetTool Start in {update_time}")

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
            save_hosts_content.append(formatedEntry)
            rprint(f"+ {formatedEntry}")

        # 3. 添加项目描述
        new_content.extend(
            [
                f"\n# Update time: {update_time}",
                "# GitHub仓库: https://github.com/sinspired/cnNetTool",
                "# cnNetTool End\n",
            ]
        )
        save_hosts_content.extend(
            [
                f"\n# Update time: {update_time}",
                "# GitHub仓库: https://github.com/sinspired/cnNetTool",
                "# cnNetTool End\n",
            ]
        )

        # 4. 写入hosts文件
        with open(self.hosts_file_path, "w") as f:
            f.write("\n".join(new_content))

        # 保存 hosts 文本
        with open("hosts", "w") as f:
            f.write("\n".join(save_hosts_content))
            rprint(
                f"\n[blue]已生成 hosts 文件,位于: [underline]hosts[/underline][/blue] (共 {len(new_entries)} 个条目)"
            )

        if not getattr(sys, "frozen", False):
            # 如果未打包为可执行程序
            Utils.write_readme_file(
                save_hosts_content, "README_template.md", f"{update_time}"
            )


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
        # 添加并发限制
        self.semaphore = asyncio.Semaphore(200)  # 限制并发请求数

        # 添加进度显示实例
        self.progress = Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            TimeRemainingColumn(),
        )

    async def _resolve_domains_batch(
        self, domains: List[str], resolve_task_id: TaskID
    ) -> Dict[str, Set[str]]:
        """批量解析域名，带进度更新"""
        results = {}
        total_domains = len(domains)

        # 更新进度条描述和总数
        if self.progress and resolve_task_id:
            self.progress.update(
                resolve_task_id,
                total=total_domains,
            )

        async with self.semaphore:
            for i, domain in enumerate(domains, 1):
                try:
                    ips = await self.resolver.resolve_domain(domain)
                    results[domain] = ips
                except Exception as e:
                    logging.error(f"解析域名 {domain} 失败: {e}")
                    results[domain] = set()

                # 更新进度
                self.progress.update(
                    resolve_task_id,
                    advance=1,
                    visible=True,
                )

        if self.progress and resolve_task_id:
            # 确保进度完结
            self.progress.update(
                resolve_task_id,
                completed=total_domains,
                visible=True,
            )
        return results

    async def _process_domain_group(self, group: DomainGroup, index: int) -> List[str]:
        """处理单个域名组"""
        entries = []
        all_ips = group.ips.copy()

        # 创建 seperateGroup 的主进度任务
        seperateGroup_task_id = self.progress.add_task(
            f"处理组 {group.name}",
            total=len(group.domains),
            visible=False,
        )

        # 创建 shareGroup 的主进度任务
        shareGroup_task_id = self.progress.add_task(
            f"处理组 {group.name}",
            total=100,
            visible=False,
        )

        # 为 _resolve_domains_batch 设置 [域名解析] 子任务进度显示
        resolve_task_id = self.progress.add_task(
            f"- [域名解析] {group.name}",
            total=0,  # 初始设为0，后续会更新
            visible=False,  # 初始隐藏，等需要时显示
        )

        # 为 LatencyTester 设置子任务进度显示
        latency_task_id = self.progress.add_task(
            f"- [测试延迟] {group.name}",
            total=0,  # 初始设为0，后续会更新
            visible=False,  # 初始隐藏，等需要时显示
        )

        self.tester.set_progress(self.progress, latency_task_id)

        if group.group_type == GroupType.SEPARATE:
            for domain in group.domains:
                resolved_ips = await self._resolve_domains_batch(
                    [domain], resolve_task_id
                )
                domain_ips = resolved_ips.get(domain, set())

                # 隐藏域名解析进度条
                self.progress.update(resolve_task_id, visible=False)

                if not domain_ips:
                    logging.warning(f"{domain} 未解析到任何可用IP。跳过该域名。")
                    continue

                fastest_ips = await self.tester.get_lowest_latency_hosts(
                    group.name,
                    [domain],
                    domain_ips,
                    self.resolver.max_latency,
                    latency_task_id,
                )
                if fastest_ips:
                    entries.extend(f"{ip}\t{domain}" for ip, latency in fastest_ips)
                else:
                    logging.warning(f"{domain} 未发现满足延迟检测要求的IP。")
                # 隐藏延迟测试进度条
                self.progress.update(latency_task_id, visible=False)
                # 主进度更新
                self.progress.update(
                    seperateGroup_task_id,
                    advance=1,
                    visible=True,
                )

                self.progress.update(
                    seperateGroup_task_id,
                    visible=False,
                )

            # 标记该组处理完成
            self.progress.update(
                seperateGroup_task_id,
                description=f"处理组 {group.name}",
                completed=len(group.domains),
                visible=True,
            )

        else:
            # 共用主机的域名组
            resolved_ips_dict = await self._resolve_domains_batch(
                group.domains, resolve_task_id
            )
            # 隐藏域名解析进度条
            self.progress.update(resolve_task_id, visible=False)
            self.progress.update(
                shareGroup_task_id,
                visible=True,
                advance=40,
            )

            for ips in resolved_ips_dict.values():
                all_ips.update(ips)

            if not all_ips:
                logging.warning(f"组 {group.name} 未解析到任何可用IP。跳过该组。")
                return entries

            logging.debug(f"组 {group.name} 解析到 {len(all_ips)} 个 DNS 主机记录")

            fastest_ips = await self.tester.get_lowest_latency_hosts(
                group.name,
                group.domains,
                all_ips,
                self.resolver.max_latency,
                latency_task_id,
            )
            self.progress.update(
                shareGroup_task_id,
                visible=True,
                advance=40,
            )

            # 隐藏延迟测试进度条
            self.progress.update(latency_task_id, visible=False)

            if fastest_ips:
                for domain in group.domains:
                    entries.extend(f"{ip}\t{domain}" for ip, latency in fastest_ips)
                    # logging.info(f"已处理域名: {domain}")
            else:
                logging.warning(f"组 {group.name} 未发现满足延迟检测要求的IP。")

            self.progress.update(
                shareGroup_task_id,
                visible=True,
                advance=20,
            )

        return entries

    async def update_hosts(self):
        """主更新函数，支持并发进度显示"""

        with self.progress:
            # 并发处理所有组
            tasks = [
                self._process_domain_group(group, i)
                for i, group in enumerate(self.domain_groups, 1)
            ]

            all_entries_lists = await asyncio.gather(*tasks)
            all_entries = [entry for entries in all_entries_lists for entry in entries]

        if all_entries:
            self.hosts_manager.write_to_hosts_file(all_entries)
            rprint(Utils.get_formatted_output("Hosts文件更新[完成]"))
        else:
            logging.warning("没有有效条目可写入")
            rprint("[bold red]警告: 没有有效条目可写入。hosts文件未更新。[/bold red]")


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
            name="GitHub Services",
            group_type=GroupType.SEPARATE,
            domains=[
                "alive.github.com",
                "api.github.com",
                "central.github.com",
                "codeload.github.com",
                "collector.github.com",
                "gist.github.com",
                "github.com",
                "github.community",
                "github.global.ssl.fastly.net",
                "github-com.s3.amazonaws.com",
                "github-production-release-asset-2e65be.s3.amazonaws.com",
                "live.github.com",
                "pipelines.actions.githubusercontent.com",
                "github.githubassets.com",
            ],
            ips={},
        ),
        DomainGroup(
            name="GitHub Asset",
            group_type=GroupType.SHARED,
            domains=[
                "github.io",
                "githubstatus.com",
                "assets-cdn.github.com",
            ],
            ips={},
        ),
        DomainGroup(
            name="GitHub Static",
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
                "media.githubusercontent.com",
                "objects.githubusercontent.com",
                "private-user-images.githubusercontent.com",
                "raw.githubusercontent.com",
                "user-images.githubusercontent.com",
            ],
            ips={},
        ),
        DomainGroup(
            name="TMDB API",
            domains=[
                "tmdb.org",
                "api.tmdb.org",
                "files.tmdb.org",
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
            name="IMDB 网页",
            group_type=GroupType.SEPARATE,
            domains=[
                "imdb.com",
                "www.imdb.com",
                "secure.imdb.com",
                "s.media-imdb.com",
                "us.dd.imdb.com",
                "www.imdb.to",
                "imdb-webservice.amazon.com",
                "origin-www.imdb.com",
            ],
            ips={},
        ),
        DomainGroup(
            name="IMDB CDN",
            group_type=GroupType.SEPARATE,
            domains=[
                "m.media-amazon.com",
                "Images-na.ssl-images-amazon.com",
                "images-fe.ssl-images-amazon.com",
                "images-eu.ssl-images-amazon.com",
                "ia.media-imdb.com",
                "f.media-amazon.com",
                "imdb-video.media-imdb.com",
                "dqpnq362acqdi.cloudfront.net",
            ],
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
                "108.177.127.214",
                "108.177.97.141",
                "142.250.101.157",
                "142.250.110.102",
                "142.250.141.100",
                "142.250.145.113",
                "142.250.145.139",
                "142.250.157.133",
                "142.250.157.149",
                "142.250.176.6",
                "142.250.181.232",
                "142.250.183.106",
                "142.250.187.139",
                "142.250.189.6",
                "142.250.196.174",
                "142.250.199.161",
                "142.250.199.75",
                "142.250.204.37",
                "142.250.204.38",
                "142.250.204.49",
                "142.250.27.113",
                "142.250.4.136",
                "142.250.66.10",
                "142.250.76.35",
                "142.251.1.102",
                "142.251.1.136",
                "142.251.163.91",
                "142.251.165.101",
                "142.251.165.104",
                "142.251.165.106",
                "142.251.165.107",
                "142.251.165.110",
                "142.251.165.112",
                "142.251.165.122",
                "142.251.165.133",
                "142.251.165.139",
                "142.251.165.146",
                "142.251.165.152",
                "142.251.165.155",
                "142.251.165.164",
                "142.251.165.165",
                "142.251.165.193",
                "142.251.165.195",
                "142.251.165.197",
                "142.251.165.201",
                "142.251.165.82",
                "142.251.165.94",
                "142.251.178.105",
                "142.251.178.110",
                "142.251.178.114",
                "142.251.178.117",
                "142.251.178.122",
                "142.251.178.137",
                "142.251.178.146",
                "142.251.178.164",
                "142.251.178.166",
                "142.251.178.181",
                "142.251.178.190",
                "142.251.178.195",
                "142.251.178.197",
                "142.251.178.199",
                "142.251.178.200",
                "142.251.178.214",
                "142.251.178.83",
                "142.251.178.84",
                "142.251.178.88",
                "142.251.178.92",
                "142.251.178.99",
                "142.251.2.139",
                "142.251.221.121",
                "142.251.221.129",
                "142.251.221.138",
                "142.251.221.98",
                "142.251.40.104",
                "142.251.41.14",
                "142.251.41.36",
                "142.251.42.197",
                "142.251.8.155",
                "142.251.8.189",
                "172.217.16.210",
                "172.217.164.103",
                "172.217.168.203",
                "172.217.168.215",
                "172.217.168.227",
                "172.217.169.138",
                "172.217.17.104",
                "172.217.171.228",
                "172.217.175.23",
                "172.217.19.72",
                "172.217.192.149",
                "172.217.192.92",
                "172.217.197.156",
                "172.217.197.91",
                "172.217.204.104",
                "172.217.204.156",
                "172.217.214.112",
                "172.217.218.133",
                "172.217.222.92",
                "172.217.31.136",
                "172.217.31.142",
                "172.217.31.163",
                "172.217.31.168",
                "172.217.31.174",
                "172.253.117.118",
                "172.253.122.154",
                "172.253.62.88",
                "173.194.199.94",
                "173.194.216.102",
                "173.194.220.101",
                "173.194.220.138",
                "173.194.221.101",
                "173.194.222.106",
                "173.194.222.138",
                "173.194.66.137",
                "173.194.67.101",
                "173.194.68.97",
                "173.194.73.106",
                "173.194.73.189",
                "173.194.76.107",
                "173.194.77.81",
                "173.194.79.200",
                "209.85.201.155",
                "209.85.201.198",
                "209.85.201.201",
                "209.85.203.198",
                "209.85.232.101",
                "209.85.232.110",
                "209.85.232.133",
                "209.85.232.195",
                "209.85.233.100",
                "209.85.233.102",
                "209.85.233.105",
                "209.85.233.136",
                "209.85.233.191",
                "209.85.233.93",
                "216.239.32.40",
                "216.58.200.10",
                "216.58.213.8",
                "34.105.140.105",
                "34.128.8.104",
                "34.128.8.40",
                "34.128.8.55",
                "34.128.8.64",
                "34.128.8.70",
                "34.128.8.71",
                "34.128.8.85",
                "34.128.8.97",
                "35.196.72.166",
                "35.228.152.85",
                "35.228.168.221",
                "35.228.195.190",
                "35.228.40.236",
                "64.233.162.102",
                "64.233.163.97",
                "64.233.165.132",
                "64.233.165.97",
                "64.233.169.100",
                "64.233.188.155",
                "64.233.189.133",
                "64.233.189.148",
                "66.102.1.167",
                "66.102.1.88",
                "74.125.133.155",
                "74.125.135.17",
                "74.125.139.97",
                "74.125.142.116",
                "74.125.193.152",
                "74.125.196.195",
                "74.125.201.91",
                "74.125.204.101",
                "74.125.204.113",
                "74.125.204.114",
                "74.125.204.132",
                "74.125.204.141",
                "74.125.204.147",
                "74.125.206.117",
                "74.125.206.137",
                "74.125.206.144",
                "74.125.206.146",
                "74.125.206.154",
                "74.125.21.191",
                "74.125.71.145",
                "74.125.71.152",
                "74.125.71.199",
                "2404:6800:4008:c13::5a",
                "2404:6800:4008:c15::94",
                "2607:f8b0:4004:c07::66",
                "2607:f8b0:4004:c07::71",
                "2607:f8b0:4004:c07::8a",
                "2607:f8b0:4004:c07::8b",
                "2a00:1450:4001:829::201a",
            },
        ),
        DomainGroup(
            name="JetBrain 插件",
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
        if getattr(sys, "frozen", False):
            # 打包后的执行文件路径
            # current_dir = Path(sys.executable).resolve().parent
            # dns_cache_dir = current_dir / "dns_cache"

            # 获取用户目录下的 .setHosts，以防止没有写入权限
            dns_cache_dir = (
                Path(os.getenv("USERPROFILE", os.getenv("HOME")))
                / ".setHosts"
                / "dns_cache"
            )
        else:
            # 脚本运行时路径
            current_dir = Path(__file__).resolve().parent
            dns_cache_dir = current_dir / "dns_cache"

        dns_cache_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在

        # (提示：dns_records.json 文件将存储 A、AAAA 等 DNS 资源记录缓存。)
        return dns_cache_dir / "dns_records.json"  # 返回缓存文件路径


# -------------------- 主函数入口 -------------------- #
async def main():
    rprint(Utils.get_formatted_line())  # 默认绿色横线
    rprint(Utils.get_formatted_output("启动 setHosts 自动更新···"))
    rprint(Utils.get_formatted_line())  # 默认绿色横线
    print()

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
    tester = LatencyTester(hosts_num=args.hosts_num)

    # 3.Hosts文件操作
    hosts_manager = HostsManager()

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

    if getattr(sys, "frozen", False):
        # 如果打包为可执行程序时
        input("\n任务执行完毕，按任意键退出！")


if __name__ == "__main__":
    asyncio.run(main())
