import argparse
import asyncio
import concurrent
import ctypes
import json
import logging
import logging.config
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

import dns.resolver
import httpx
import wcwidth
from rich import print as rprint

# from rich.progress import Progress, SpinnerColumn, TextColumn

# -------------------- 常量设置 -------------------- #
RESOLVER_TIMEOUT = 0.1  # DNS 解析超时时间 秒
HOSTS_NUM = 1  # 每个域名限定Hosts主机 ipv4 数量
MAX_LATENCY = 500  # 允许的最大延迟
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
            "发布: 2024-12-06\n"
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
        "-size",
        "--batch-size",
        default=5,
        type=int,
        help="SSL证书验证批次",
    )
    parser.add_argument(
        "-policy",
        "--dns-resolve-policy",
        default="all",
        type=str,
        help="DNS解析器区域选择,[all、global、china]",
    )
    parser.add_argument(
        "-n",
        "--checkonly",
        action="store_true",
        help="仅检测，不自动设置",
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
        base_str = f"正在处理第 {i} 组域名： {group_name.upper()}"

        # 计算基础字符串的显示宽度
        base_width = wcwidth.wcswidth(base_str)

        # 计算需要添加的空格数量
        # 需要考虑Rich标签不计入显示宽度
        padding_needed = ref_width - base_width

        # 确保填充不会为负数
        padding_needed = max(0, padding_needed)

        # 构建最终的格式化字符串
        formatted_str = f"\n[bold white on bright_black]正在处理第 [green]{i}[/green] 组域名： {group_name.upper()}{' ' * padding_needed}[/bold white on bright_black]"

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
        start_time = datetime.now()
        ips = set()

        # 1. 首先通过常规DNS服务器解析
        dns_ips = await self._resolve_via_dns(domain, "all")
        ips.update(dns_ips)

        dns_resolve_end_time = datetime.now()

        dns_resolve_duration = dns_resolve_end_time - start_time
        logging.debug(f"DNS解析耗时: {dns_resolve_duration.total_seconds():.2f}秒")

        # # 2. 然后通过DNS_records解析
        # # 由于init时已经处理了过期文件，这里只需要检查域名是否在缓存中
        # if domain in self.dns_records:
        #     domain_hosts = self.dns_records.get(domain, {})
        #     ipv4_ips = domain_hosts.get("ipv4", [])
        #     ipv6_ips = domain_hosts.get("ipv6", [])

        #     ips.update(ipv4_ips + ipv6_ips)
        #     logging.debug(
        #         f"成功通过缓存文件解析 {domain}, 发现 {len(ipv4_ips) + len(ipv6_ips)} 个 DNS 主机:\n{ipv4_ips}\n{ipv6_ips if ipv6_ips else ''}\n"
        #     )
        # else:
        #     ipaddress_ips = await self._resolve_via_ipaddress(domain)
        #     if ipaddress_ips:
        #         ips.update(ipaddress_ips)

        # if ips:
        #     logging.debug(
        #         f"成功通过 DNS服务器 和 DNS记录 解析 {domain}, 发现 {len(ips)} 个 唯一 DNS 主机\n{ips}\n"
        #     )
        # else:
        #     logging.debug(f"警告: 无法解析 {domain}")

        ipaddress_resolve_end_time = datetime.now()
        ipaddress_resolve_duration = ipaddress_resolve_end_time - dns_resolve_end_time
        total_resolve_duration = ipaddress_resolve_end_time - start_time

        logging.debug(
            f"IP地址解析耗时: {ipaddress_resolve_duration.total_seconds():.2f}秒"
        )
        logging.debug(f"DNS解析总耗时: {total_resolve_duration.total_seconds():.2f}秒")

        return ips

    async def _resolve_via_dns(self, domain: str, dns_type: str = "all") -> Set[str]:
        """
        通过 DNS 解析域名

        :param domain: 待解析的域名
        :param dns_type: 解析使用的 DNS 类型。可选值：
            - "all": 同时使用国内和国际 DNS
            - "china": 仅使用国内 DNS
            - "international": 仅使用国际 DNS
        :return: 解析得到的 IP 集合
        """

        async def resolve_with_dns_server(dns_server_info: dict) -> Set[str]:
            """单个DNS服务器的解析协程"""
            dns_server = dns_server_info["ip"]
            dns_provider = dns_server_info["provider"]
            ips = set()
            resolver = dns.resolver.Resolver(configure=False)
            resolver.nameservers = [dns_server]
            resolver.timeout = RESOLVER_TIMEOUT
            resolver.lifetime = RESOLVER_TIMEOUT

            try:
                # 使用 to_thread 在线程池中执行同步的 DNS 查询
                for qtype in ["A", "AAAA"]:
                    try:
                        answers = await asyncio.to_thread(
                            resolver.resolve, domain, qtype
                        )
                        ips.update(answer.address for answer in answers)
                    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                        pass
                    except Exception as e:
                        logging.debug(f"DNS 查询异常 ({qtype}, {dns_server}): {e}")

                if ips:
                    logging.debug(
                        f"成功使用 {dns_provider} : {dns_server} 解析 {domain}，共 {len(ips)} 个主机: {ips}"
                    )

                return ips

            except Exception as e:
                logging.debug(f"使用 {dns_server} 解析 {domain} 失败: {e}")
                return set()

        # 根据 dns_type 选择要使用的 DNS 服务器
        if dns_type.lower() == "all":
            dns_servers = (
                self.dns_servers["china_mainland"] + self.dns_servers["international"]
            )
        elif dns_type.lower() == "china":
            dns_servers = self.dns_servers["china_mainland"]
        elif dns_type.lower() == "global" or dns_type.lower() == "international":
            dns_servers = self.dns_servers["international"]
        else:
            dns_servers = (
                self.dns_servers["china_mainland"] + self.dns_servers["international"]
            )
            # raise ValueError(f"无效的 DNS 类型：{dns_type}")

        # 并发解析所有选定的 DNS 服务器，并保留非空结果
        tasks = [resolve_with_dns_server(dns_server) for dns_server in dns_servers]
        results = await asyncio.gather(*tasks)

        # 合并所有非空的解析结果
        ips = set(ip for result in results for ip in result if ip)
        if ips:
            logging.debug(
                f"成功使用多个 DNS 服务器解析 {domain}，共 {len(ips)} 个主机:\n{ips}\n"
            )
        # input("按任意键继续")
        return ips

    def retry_async(tries=3, delay=0):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                domain = args[1]
                for attempt in range(tries):
                    try:
                        return await func(*args, **kwargs)
                    except Exception:
                        if attempt < tries - 1:
                            print(f"第 {attempt + 2} 次尝试:")
                            # logging.debug(f"通过DNS_records解析 {args[1]},第 {attempt + 2} 次尝试:")
                        if attempt == tries - 1:
                            self = args[0]  # 明确 self 的引用
                            domain = args[1]
                            current_time = datetime.now().isoformat()
                            self.dns_records[domain] = {
                                "last_update": current_time,
                                "ipv4": [],
                                "ipv6": [],
                                "source": "DNS_records",
                            }
                            self.save_hosts_cache()
                            logging.warning(
                                f"ipaddress.com {tries} 次尝试后未解析到 {domain} 的 DNS_records 地址，"
                                f"已写入空地址到缓存以免无谓消耗网络资源"
                            )
                            # print(f"通过 DNS_records 解析 {
                            #       domain}，{tries} 次尝试后终止！")
                            return None
                        await asyncio.sleep(delay)
                return None

            return wrapper

        return decorator

    LOGGING_CONFIG = {
        "version": 1,
        "handlers": {
            "httpxHandlers": {
                "class": "logging.StreamHandler",
                "formatter": "http",
                "stream": "ext://sys.stderr",
            }
        },
        "formatters": {
            "http": {
                "format": "%(levelname)s [%(asctime)s] %(name)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "loggers": {
            "httpx": {
                "handlers": ["httpxHandlers"],
                "level": "WARNING",
            },
            "httpcore": {
                "handlers": ["httpxHandlers"],
                "level": "WARNING",
            },
        },
    }

    logging.config.dictConfig(LOGGING_CONFIG)

    @retry_async(tries=3)
    async def _resolve_via_ipaddress(self, domain: str) -> Set[str]:
        ips = set()
        url = f"https://www.ipaddress.com/website/{domain}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/106.0.0.0 Safari/537.36"
        }
        # headers = {
        #     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.5615.121 Safari/537.36",
        #     "Referer": "https://www.ipaddress.com",
        # }

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(1.0),
                follow_redirects=True,
                http2=True,
            ) as client:
                response = await client.get(url, headers=headers)

                # # 使用内置方法检查状态码
                response.raise_for_status()  # 自动处理非200状态码

                content = response.text

                ipv4_pattern = r">((?:[0-9]{1,3}\.){3}[0-9]{1,3})\b<"
                # ipv6_pattern = r">((?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4})<"
                # 支持ipv6压缩
                ipv6_pattern = r">((?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}|[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,5}::[0-9a-fA-F]{1,6})<"

                ipv4_ips = set(re.findall(ipv4_pattern, content))
                ipv6_ips = set(re.findall(ipv6_pattern, content))

                ips.update(ipv4_ips)
                ips.update(ipv6_ips)

                if ips:
                    current_time = datetime.now().isoformat()
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
                        "last_update": datetime.now().isoformat(),
                        "ipv4": [],
                        "ipv6": [],
                        "source": "DNS_records",
                    }
                    self.save_hosts_cache()
                    logging.warning(
                        f"ipaddress.com 未解析到 {domain} 的 DNS_records 地址,已写入空地址到缓存以免无谓消耗网络资源"
                    )
        except Exception as e:
            logging.error(f"通过DNS_records解析 {domain} 失败! {e}")
            raise
        return ips


# -------------------- 延迟测速模块 -------------------- #


class LatencyTester:
    def __init__(self, hosts_num: int, max_workers: int = 200):
        self.hosts_num = hosts_num
        self.max_workers = max_workers

    async def get_lowest_latency_hosts(
        self,
        group_name: str,
        domains: List[str],
        file_ips: Set[str],
        latency_limit: int,
    ) -> List[Tuple[str, float]]:
        """
        使用线程池和异步操作优化IP延迟和SSL证书验证
        """
        all_ips = list(file_ips)
        # start_time = datetime.now()
        rprint(
            f"[bright_black]- 获取到 [bold bright_green]{len(all_ips)}[/bold bright_green] 个唯一IP地址[/bright_black]"
        )
        if all_ips:
            rprint("[bright_black]- 检测主机延迟...[/bright_black]")

        # 使用线程池来并发处理SSL证书验证
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:
            # 第一步：并发获取IP延迟
            ping_tasks = [self.get_host_average_latency(ip) for ip in all_ips]
            latency_results = await asyncio.gather(*ping_tasks)

            # 筛选有效延迟的IP
            valid_latency_results = [
                result for result in latency_results if result[1] != float("inf")
            ]
            if valid_latency_results:
                if len(valid_latency_results) < len(all_ips):
                    rprint(
                        f"[bright_black]- 检测到 [bold bright_green]{len(valid_latency_results)}[/bold bright_green] 个有效IP地址[/bright_black]"
                    )
                valid_latency_ips = [
                    result
                    for result in valid_latency_results
                    if result[1] < latency_limit
                ]
                if not valid_latency_ips:
                    logging.warning(f"未发现延迟小于 {latency_limit}ms 的IP。")
                    min_result = [min(valid_latency_results, key=lambda x: x[1])]
                    latency_limit = min_result[0][1] * 2
                    logging.debug(f"主机IP最低延迟 {latency_limit:.0f}ms")
                    valid_latency_ips = [
                        result
                        for result in valid_latency_results
                        if result[1] <= latency_limit
                    ]
            else:
                rprint("[red]延迟检测没有获得有效IP[/red]")
                return []

            # 排序结果
            valid_latency_ips = sorted(valid_latency_ips, key=lambda x: x[1])

            if len(valid_latency_ips) < len(valid_latency_results):
                rprint(
                    f"[bright_black]- 检测到 [bold bright_green]{len(valid_latency_ips)}[/bold bright_green] 个延迟小于 {latency_limit}ms 的有效IP地址[/bright_black]"
                )

            ipv4_results = [r for r in valid_latency_ips if not Utils.is_ipv6(r[0])]
            ipv6_results = [r for r in valid_latency_ips if Utils.is_ipv6(r[0])]

            # 第二步：使用线程池并发验证SSL证书
            # if "github" in group_name.lower():
            if len(valid_latency_ips) > 1 and any(
                keyword in group_name.lower() for keyword in ["google"]
            ):
                rprint("[bright_black]- 验证SSL证书...[/bright_black]")
                ipv4_count = 0
                ipv6_count = 0
                batch_size = args.batch_size
                total_results = len(valid_latency_ips)
                valid_results = []

                loop = asyncio.get_running_loop()

                for i in range(0, total_results, batch_size):
                    min_len = min(total_results, batch_size)
                    batch = valid_latency_ips[i : i + min_len]
                    ssl_verification_tasks = [
                        loop.run_in_executor(
                            executor,
                            self._sync_is_cert_valid_dict,
                            domains[0],
                            ip,
                            latency,
                        )
                        for ip, latency in batch
                    ]

                    for future in asyncio.as_completed(ssl_verification_tasks):
                        ip, latency, ssl_valid = await future
                        if ssl_valid:
                            valid_results.append((ip, latency))
                            if Utils.is_ipv6(ip):
                                ipv6_count += 1
                            else:
                                ipv4_count += 1
                            if ipv6_results:
                                if ipv4_results:
                                    if ipv6_count >= 1 and ipv4_count >= 1:
                                        break
                                else:
                                    if ipv6_count >= 1:
                                        break
                            else:
                                if ipv4_count >= self.hosts_num:
                                    break
                    if ipv6_results:
                        if ipv4_results:
                            if ipv6_count >= 1 and ipv4_count >= 1:
                                break
                        else:
                            if ipv6_count >= 1:
                                break
                    else:
                        if ipv4_count >= self.hosts_num:
                            break
            else:
                valid_results = valid_latency_ips

        # 按延迟排序并选择最佳主机
        valid_results = sorted(valid_results, key=lambda x: x[1])

        if not valid_results:
            rprint(f"[red]未发现延迟小于 {latency_limit}ms 且证书有效的IP。[/red]")

        # 选择最佳主机（支持IPv4和IPv6）
        best_hosts = self._select_best_hosts(valid_results)

        # 打印结果（可以根据需要保留或修改原有的打印逻辑）
        self._print_results(best_hosts, latency_limit)

        return best_hosts

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

    def _sync_is_cert_valid_dict(
        self, domain: str, ip: str, latency: float, port: int = 443
    ) -> Tuple[str, float, bool]:
        """
        同步版本的证书验证方法，用于在线程池中执行
        """
        try:
            context = ssl.create_default_context()
            context.verify_mode = ssl.CERT_REQUIRED
            context.check_hostname = True

            with socket.create_connection((ip, port), timeout=2) as sock:
                with context.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
                    not_after = datetime.strptime(
                        cert["notAfter"], "%b %d %H:%M:%S %Y %Z"
                    )
                    if not_after < datetime.now():
                        logging.debug(f"{domain} ({ip}) {latency:.0f}ms: 证书已过期")
                        return (ip, latency, False)

                    logging.debug(
                        f"{domain} ({ip}) {latency:.0f}ms: SSL证书有效，截止日期为 {not_after}"
                    )
                    return (ip, latency, True)

        except ConnectionError as e:
            logging.debug(
                f"{domain} ({ip}) {latency:.0f}ms: 连接被强迫关闭，ip有效 - {e}"
            )
            return (ip, latency, True)
        except Exception as e:
            logging.debug(f"{domain} ({ip}) {latency:.0f}ms: 证书验证失败 - {e}")
            return (ip, latency, False)

    def _sync_is_cert_valid_dict_average(
        self, domains: List[str], ip: str, latency: float, port: int = 443
    ) -> Tuple[str, float, bool]:
        """
        同步版本的证书验证方法，用于在线程池中执行。
        任意一个 domain 验证通过就视为通过。
        """
        for domain in domains:
            try:
                context = ssl.create_default_context()
                context.verify_mode = ssl.CERT_REQUIRED
                context.check_hostname = True

                with socket.create_connection((ip, port), timeout=2) as sock:
                    with context.wrap_socket(sock, server_hostname=domain) as ssock:
                        cert = ssock.getpeercert()
                        not_after = datetime.strptime(
                            cert["notAfter"], "%b %d %H:%M:%S %Y %Z"
                        )
                        if not_after < datetime.now():
                            logging.debug(
                                f"{domain} ({ip}) {latency:.0f}ms: 证书已过期"
                            )
                            continue  # 检查下一个 domain

                        logging.debug(
                            f"{domain} ({ip}) {latency:.0f}ms: SSL证书有效，截止日期为 {not_after}"
                        )
                        return (ip, latency, True)  # 任意一个验证通过即返回成功

            except ConnectionError as e:
                logging.debug(
                    f"{domain} ({ip}) {latency:.0f}ms: 连接被强迫关闭，ip有效 - {e}"
                )
                return (ip, latency, True)
            except Exception as e:
                logging.debug(f"{domain} ({ip}) {latency:.0f}ms: 证书验证失败 - {e}")
                continue  # 检查下一个 domain

        # 如果所有 domain 都验证失败
        return (ip, latency, False)

    def _select_best_hosts(
        self, valid_results: List[Tuple[str, float]]
    ) -> List[Tuple[str, float]]:
        """
        选择最佳主机，优先考虑IPv4和IPv6
        """
        ipv4_results = [r for r in valid_results if not Utils.is_ipv6(r[0])]
        ipv6_results = [r for r in valid_results if Utils.is_ipv6(r[0])]

        best_hosts = []
        selected_count = 0

        if ipv4_results:
            min_ipv4_results = min(ipv4_results, key=lambda x: x[1])

        # 先选择IPv4
        if ipv4_results:
            logging.debug(f"有效IPv4：\n{ipv4_results}\n")
            for ip, latency in ipv4_results:
                best_hosts.append((ip, latency))
                selected_count += 1
                if (
                    ipv6_results and selected_count >= 1
                ) or selected_count >= self.hosts_num:
                    break
        # 再选择IPv6
        if ipv6_results:
            logging.debug(f"有效IPv6：\n{ipv6_results}\n")
            for ip, latency in ipv6_results:
                if ipv4_results and latency <= min_ipv4_results[1] * 2:
                    best_hosts.append((ip, latency))
                    break
                else:
                    best_hosts.append((ip, latency))
                    break

        return best_hosts

    def _print_results(self, best_hosts: List[Tuple[str, float]], latency_limit: int):
        """
        打印结果的方法
        """
        rprint(
            f"[bold yellow]最快的 DNS主机 IP（优先选择 IPv6） 丨   延迟 < {latency_limit:.0f}ms ：[/bold yellow]"
        )
        for ip, time in best_hosts:
            rprint(
                f"  [green]{ip}[/green]    [bright_black]{time:.2f} ms[/bright_black]"
            )

        # end_time = datetime.now()
        # total_time = end_time - start_time
        # rprint(
        #     f"[bright_black]- 运行时间:[/bright_black] [cyan]{total_time.total_seconds():.2f} 秒[/cyan]")


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

    async def update_hosts(self):
        # 更新hosts文件的主逻辑
        all_entries = []

        for i, group in enumerate(self.domain_groups, 1):
            progress_str = Utils.get_align_str(i, group.name)
            rprint(progress_str)
            # 先获取预设IP
            default_ips = group.ips.copy()

            # 2. 根据不同组设置IP
            if group.group_type == GroupType.SEPARATE:
                for domain in group.domains:
                    rprint(f"\n为域名 {domain} 设置 DNS 映射主机")
                    # 重置初始ip，否则会混淆
                    all_ips = set()
                    if default_ips:
                        rprint(
                            f"[bright_black]- 读取到 [bold bright_green]{len(default_ips)}[/bold bright_green] 个预设IP地址[/bright_black]"
                        )
                        all_ips.update(default_ips)

                    resolved_ips = await self.resolver.resolve_domain(domain)
                    all_ips.update(resolved_ips)

                    if not all_ips:
                        logging.warning(f"{domain} 未找到任何可用IP。跳过该域名。")
                        continue

                    fastest_ips = set()
                    fastest_ips = await self.tester.get_lowest_latency_hosts(
                        group.name,
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
                all_ips = set()
                if default_ips:
                    rprint(
                        f"[bright_black]- 读取到 [bold bright_green]{len(default_ips)}[/bold bright_green] 个预设IP地址[/bright_black]"
                    )
                    all_ips.update(default_ips)

                # 收集组内所有域名的DNS解析结果
                domain_resolve_tasks = [
                    self.resolver.resolve_domain(domain) for domain in group.domains
                ]
                resolved_ips = await asyncio.gather(
                    *domain_resolve_tasks, return_exceptions=True
                )

                all_ips.update(ip for ip_list in resolved_ips for ip in ip_list if ip)

                if not all_ips:
                    logging.warning(f"组 {group.name} 未找到任何可用IP。跳过该组。")
                    continue

                # rprint(f"  找到 {len(all_ips)} 个 DNS 主机记录")

                fastest_ips = await self.tester.get_lowest_latency_hosts(
                    group.name,
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
            name="GitHub Services",
            group_type=GroupType.SEPARATE,
            domains=[
                "alive.github.com",
                "live.github.com",
                "api.github.com",
                "codeload.github.com",
                "central.github.com",
                "gist.github.com",
                "github.com",
                "github.community",
                "github.global.ssl.fastly.net",
                "github-com.s3.amazonaws.com",
                "github-production-release-asset-2e65be.s3.amazonaws.com",
                "github-production-user-asset-6210df.s3.amazonaws.com",
                "github-production-repository-file-5c1aeb.s3.amazonaws.com",
                "pipelines.actions.githubusercontent.com",
                "github.githubassets.com",
                "github-cloud.s3.amazonaws.com",
                "github.blog",
            ],
            ips={},
        ),
        DomainGroup(
            name="GitHub Asset",
            group_type=GroupType.SHARED,
            domains=[
                "githubstatus.com",
                "assets-cdn.github.com",
                "github.io",
            ],
            ips={},
        ),
        DomainGroup(
            name="GitHub Central&Education ",
            group_type=GroupType.SHARED,
            domains=[
                "collector.github.com",
                "education.github.com",
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
            name="TMDB themoviedb",
            group_type=GroupType.SHARED,
            domains=[
                "tmdb.org",
                "api.tmdb.org",
                "files.tmdb.org",
                "themoviedb.org",
                "api.themoviedb.org",
                "www.themoviedb.org",
                "auth.themoviedb.org",
            ],
            ips={
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
            },
        ),
        DomainGroup(
            name="TMDB 封面",
            group_type=GroupType.SHARED,
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
            name="IMDB 网页",
            group_type=GroupType.SHARED,
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
            group_type=GroupType.SHARED,
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
            group_type=GroupType.SHARED,
            domains=[
                "translate.google.com",
                "translate.googleapis.com",
                "translate-pa.googleapis.com",
                "jnn-pa.googleapis.com",
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
                "2001:67c:2960:6464::d8ef:2076",
                "2001:67c:2960:6464::d8ef:2039",
                "2001:67c:2960:6464::d8ef:2038",
                "2001:67c:2960:6464::d8ef:2028",
                "2001:67c:2960:6464::d8ef:2006",
                "185.199.109.133",
                "185.199.110.133",
                "185.199.111.133",
                "172.217.70.133",
                "142.251.42.55",
                "142.251.43.87",
                "142.251.43.75",
                "142.251.43.139",
                "142.251.223.231",
                "142.251.223.183",
                "172.217.24.75",
                "172.217.24.77",
                "142.250.117.107",
                "142.250.64.181",
                "142.250.117.95",
                "142.251.43.210",
                "142.251.43.185",
                "142.251.43.88",
                "142.251.43.56",
                "142.251.43.119",
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
    DNS_SERVERS = {
        "international": [
            # 国际 DNS 服务器
            # 第 1 梯队: 延迟较低
            {"ip": "208.67.222.222", "provider": "OpenDNS", "type": "ipv4"},  # Open DNS
            {"ip": "2620:0:ccc::2", "provider": "OpenDNS", "type": "ipv6"},  # Open DNS
            {
                "ip": "2001:4860:4860::8888",  # Google Public DNS
                "provider": "Google",
                "type": "ipv6",
            },
            {
                "ip": "2001:4860:4860::8844",  # Google Public DNS
                "provider": "Google",
                "type": "ipv6",
            },
            {"ip": "210.184.24.65", "provider": "CPC HK", "type": "ipv4"},  # 香港
            {"ip": "18.163.103.200", "provider": "Amazon HK", "type": "ipv4"},  # 香港
            {
                "ip": "43.251.159.130",
                "provider": "IPTELECOM HK",  # 香港
                "type": "ipv4",
            },
            {
                "ip": "14.198.168.140",
                "provider": "Broadband HK",  # 香港
                "type": "ipv4",
            },
            {
                "ip": "66.203.146.122",
                "provider": "Dimension HK",  # 香港
                "type": "ipv4",
            },
            {"ip": "118.201.189.90", "provider": "SingNet", "type": "ipv4"},  # 新加坡
            {"ip": "1.228.180.5", "provider": "SK Broadband ", "type": "ipv4"},  # 韩国
            {"ip": "183.99.33.6", "provider": "Korea Telecom ", "type": "ipv4"},  # 韩国
            {"ip": "203.248.252.2", "provider": "LG DACOM ", "type": "ipv4"},  # 韩国
            # 第 2 梯队：延迟适中
            # {
            #     "ip": "129.250.35.250",
            #     "provider": "NTT Communications",  # 日本
            #     "type": "ipv4"
            # },
            # {
            #     "ip": "168.126.63.1",
            #     "provider": "KT DNS",  # 韩国
            #     "type": "ipv4"
            # },
            # {
            #     "ip": "101.110.50.106",
            #     "provider": "Soft Bank",
            #                 "type": "ipv4"
            # },
            # {
            #     "ip": "202.175.86.206",
            #     "provider": "Telecomunicacoes de Macau", #澳门
            #                 "type": "ipv4"
            # },
            # {
            #     "ip": "45.123.201.235",
            #     "provider": "Informacoes Tecnologia de Macau", #澳门
            #                 "type": "ipv4"
            # },
            # {
            #     "ip": "2400:6180:0:d0::5f6e:4001",
            #     "provider": "DigitalOcean",  # 新加坡
            #                 "type": "ipv6"
            # },
            # {
            #     "ip": "2a09::",  # DNS.SB 德国 2a11::
            #     "provider": "DNS.SB",
            #                 "type": "ipv6"
            # },
            # {
            #     "ip": "185.222.222.222",  # DNS.SB 德国45.11.45.11
            #     "provider": "DNS.SB",
            #                 "type": "ipv4"
            # },
            # {
            #     "ip": "9.9.9.9",  # Quad9 DNS
            #     "provider": "Quad9",
            #     "type": "ipv4"
            # },
            # {
            #     "ip": "149.112.112.112",  # Quad9 DNS
            #     "provider": "Quad9",
            #     "type": "ipv4"
            # },
            # {
            #     "ip": "208.67.222.222",  # Open DNS
            #     "provider": "OpenDNS",
            #     "type": "ipv4"
            # },
            # {
            #     "ip": "2620:0:ccc::2",  # Open DNS
            #     "provider": "OpenDNS",
            #     "type": "ipv6"
            # },
            # {
            #     "ip": "2620:fe::fe",  # Quad9
            #     "provider": "Quad9",
            #     "type": "ipv6"
            # },
            # {
            #     "ip": "2620:fe::9",  # Quad9
            #     "provider": "Quad9",
            #     "type": "ipv6"
            # },
            # {
            #     "ip": "77.88.8.1",
            #     "provider": "Yandex DNS",# 俄国
            #                 "type": "ipv4"
            # },
            # {
            #     "ip": "2a02:6b8::feed:0ff",# 俄国
            #     "provider": "Yandex DNS",
            #                 "type": "ipv6"
            # },
        ],
        "china_mainland": [
            # 国内 DNS 服务器
            # 第 1 梯队：正确解析Google翻译
            # 首选：延迟较低，相对稳定：
            {"ip": "114.114.114.114", "provider": "114DNS", "type": "ipv4"},  # 114 DNS
            {
                "ip": "1.1.8.8",  # 上海牙木科技|联通机房
                "provider": "上海牙木科技|联通机房",
                "type": "ipv4",
            },
            # 备选：延迟一般：
            # {
            #     "ip": "180.76.76.76",  # 百度
            #     "provider": "Baidu",
            #                 "type": "ipv4"
            # },
            # {
            #     "ip": "202.46.33.250",  # 上海通讯
            #     "provider": "Shanghai Communications",
            #     "type": "ipv4"
            # },
            # {
            #     "ip": "202.46.34.75",  # 上海通讯
            #     "provider": "Shanghai Communications",
            #                 "type": "ipv4"
            # },240c::6644
            # 第 2 梯队：无法正确解析Google翻译
            # {
            #     "ip": "223.5.5.5",  # 阿里云 DNS
            #     "provider": "Alibaba",
            #     "type": "ipv4"
            # },
            # {
            #     "ip": "2400:3200::1",  # 阿里云 DNS
            #     "provider": "Alibaba",
            #     "type": "ipv6"
            # },
            # {
            #     "ip": "119.29.29.29",  # DNSPod DNS
            #     "provider": "Tencent",
            #     "type": "ipv4"
            # },
            # {
            #     "ip": "2402:4e00::",  # DNSPod DNS
            #     "provider": "Tencent",
            #     "type": "ipv6"
            # },
            {"ip": "114.114.114.114", "provider": "114DNS", "type": "ipv4"},  # 114 DNS
            # {
            #     "ip": "101.226.4.6",  # 未360dns
            #     "provider": "360dns",
            #     "type": "ipv4"
            # }
        ],
    }

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
    rprint("[green]----------------------------------------------------------[/green]")
    rprint(
        "[blue on green]启动 setHosts 自动更新···                              [/blue on green]"
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
    tester = LatencyTester(hosts_num=args.hosts_num, max_workers=200)

    # 3.Hosts文件操作
    hosts_manager = HostsManager()

    # 4.初始化 Hosts更新器 参数
    updater = HostsUpdater(
        domain_groups=DOMAIN_GROUPS,
        resolver=resolver,
        tester=tester,
        hosts_manager=hosts_manager,
    )

    if not PrivilegeManager.is_admin() and not args.checkonly:
        rprint(
            "[bold red]需要管理员权限来修改hosts文件。正在尝试提升权限...[/bold red]"
        )
        PrivilegeManager.run_as_admin()

    # 启动 Hosts更新器
    if not args.checkonly:
        rprint("[blue on green]正在更新 Hosts 文件...[/blue on green]")
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
