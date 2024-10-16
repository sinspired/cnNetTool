import argparse
import asyncio
import ctypes
import sys
import dns.resolver
import socket
from ping3 import ping
from typing import List, Set, Tuple
from datetime import datetime
import time
import os
import shutil
import platform
import logging
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

console = Console()

start_time = datetime.now()

RESOLVER_TIMEOUT = 1  # DNS 解析超时时间 秒
HOSTS_NUM = 2  # 限定最快 ip 数量
MAX_LATENCY = 300  # 允许的最大延迟
PING_TIMEOUT = 1  # ping 超时时间
NUM_PINGS = 4  # ping次数

# 初始化日志模块
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Hosts文件更新工具，请使用管理员权限运行"
    )
    parser.add_argument(
        "--log-level",
        "--log",
        "--l",
        default="info",
        choices=["debug", "info", "warnning", "error"],
        help="设置日志输出等级，'DEBUG', 'INFO', 'WARNING', 'ERROR'",
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


# 定义域名和IP组的数据类
class DomainGroup:
    def __init__(self, name: str, domains: List[str], ips: Set[str] = None):
        self.name = name
        self.domains = domains if isinstance(domains, list) else [domains]
        self.ips = ips if ips else set()


class SetHosts:
    def __init__(
        self, domain_groups: List[DomainGroup], custom_dns_servers: List[str] = None
    ):
        self.hosts_file_path = self.get_hosts_file_path()
        self.domain_groups = domain_groups
        self.dns_servers = custom_dns_servers or [
            "2402:4e00::",  # DNSPod (IPv6)
            "223.5.5.5",  # Alibaba DNS (IPv4)
            "119.29.29.29",  # DNSPod (IPv4)
            "2400:3200::1",  # Alibaba DNS (IPv6)
        ]
        self.max_latency = args.max_latency
        self.hosts_num = args.hosts_num

    @staticmethod
    def get_hosts_file_path() -> str:
        os_type = platform.system().lower()
        if os_type == "windows":
            return r"C:\Windows\System32\drivers\etc\hosts"
        elif os_type in ["linux", "darwin"]:
            return "/etc/hosts"
        else:
            raise ValueError("不支持的操作系统")

    async def resolve_domain(self, domain: str) -> Set[str]:
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
                    return ips
            except Exception as e:
                logging.debug(f"使用 {dns_server} 解析 {domain} 失败: {e}")

        logging.debug(f"警告: 无法使用任何DNS服务器解析 {domain}")
        return ips

    @staticmethod
    def is_ipv6(ip: str) -> bool:
        return ":" in ip

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

    async def ping_ip(self, ip: str, port: int = 443) -> Tuple[str, float]:
        try:
            response_times = await asyncio.gather(
                *[self.test_ip_connection(ip, port) for _ in range(NUM_PINGS)]
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

    async def get_best_hosts(
        self, domains: List[str], file_ips: Set[str], latency_limit: int
    ) -> List[Tuple[str, float]]:
        all_ips = set()

        if args.log_level.upper() == "INFO":
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("正在解析域名...", total=len(domains))
                tasks = [self.resolve_domain(domain) for domain in domains]
                for ips in await asyncio.gather(*tasks):
                    all_ips.update(ips)
                    progress.update(task, advance=1)
                all_ips.update(file_ips)
        else:
            tasks = [self.resolve_domain(domain) for domain in domains]
            for ips in await asyncio.gather(*tasks):
                all_ips.update(ips)
            all_ips.update(file_ips)

        rprint(f"[bold green]找到 {len(all_ips)} 个唯一IP地址[/bold green]")

        if args.log_level.upper() == "INFO":
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("正在ping所有IP地址...", total=len(all_ips))
                ping_tasks = [self.ping_ip(ip) for ip in all_ips]
                results = []
                for result in await asyncio.gather(*ping_tasks):
                    results.append(result)
                    progress.update(task, advance=1)
        else:
            ping_tasks = [self.ping_ip(ip) for ip in all_ips]
            results = []
            for result in await asyncio.gather(*ping_tasks):
                results.append(result)

        valid_results = [result for result in results if result[1] < latency_limit]
        if not valid_results:
            logging.warning(f"未找到延迟小于 {latency_limit}ms 的IP。")
            return []

        ipv4_results = [r for r in valid_results if not self.is_ipv6(r[0])]
        ipv6_results = [r for r in valid_results if self.is_ipv6(r[0])]

        best_hosts = []
        if ipv4_results and ipv6_results:
            best_hosts.append(min(ipv4_results, key=lambda x: x[1]))
            best_hosts.append(min(ipv6_results, key=lambda x: x[1]))
        else:
            best_hosts = sorted(valid_results, key=lambda x: x[1])[: self.hosts_num]

        rprint(
            f"[bold yellow]最快的 IP 地址(如有IPv6，优先添加了IPv6) (延迟 < {latency_limit}ms):[/bold yellow]"
        )
        for ip, time in best_hosts:
            rprint(f"  [green]{ip}[/green]: [yellow]{time:.2f} ms[/yellow]")
        return best_hosts

    def backup_hosts_file(self):
        if os.path.exists(self.hosts_file_path):
            backup_path = f"{self.hosts_file_path}.bak"
            shutil.copy(self.hosts_file_path, backup_path)
            print()
            rprint(
                f"[bold blue]已备份 {self.hosts_file_path} 到 {backup_path}[/bold blue]"
            )

    def write_to_hosts_file(self, new_entries: List[str]):
        self.backup_hosts_file()

        with open(self.hosts_file_path, "r") as f:
            existing_content = f.read().splitlines()

        new_domains = set(
            entry.split()[1] for entry in new_entries if len(entry.split()) >= 2
        )

        new_content = []
        skip = False
        for line in existing_content:
            line = line.strip()

            if line.startswith("# 以下条目由 cnNetTool-SetHosts 脚本添加于"):
                skip = True

            if skip:
                if line == "" or line.startswith("#"):
                    continue
                else:
                    skip = False

            if (
                line.startswith("#") or not line
            ) and "以下条目由 cnNetTool-SetHosts 脚本添加" not in line:
                new_content.append(line)
                continue

            parts = line.split()
            if len(parts) < 2:
                new_content.append(line)
                continue

            domain = parts[1]

            if domain not in new_domains:
                new_content.append(line)
            else:
                logging.debug(f"删除旧条目: {line}")

        new_content.append(
            f""
            f"# 以下条目由 cnNetTool-SetHosts 脚本添加于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        rprint("\n[bold yellow]正在更新hosts文件...[/bold yellow]")
        for entry in new_entries:
            new_content.append(f"{entry} #mkhosts")
            rprint(f"添加条目:{entry}")

        new_content.append(f"")
        with open(self.hosts_file_path, "w") as f:
            f.write("\n".join(new_content))

    async def update_hosts(self):
        all_entries = []
        for i, group in enumerate(self.domain_groups, 1):
            rprint(f"\n[bold]正在处理第 {i} 组 {group.name}...[/bold]")

            # 1. 收集组内所有域名的DNS解析结果
            all_ips = group.ips.copy()  # 从预设IP开始
            for domain in group.domains:
                resolved_ips = await self.resolve_domain(domain)
                all_ips.update(resolved_ips)
            
            if not all_ips:
                logging.warning(f"组 {group.name} 未找到任何可用IP。跳过该组。")
                continue
                
            rprint(f"  找到 {len(all_ips)} 个候选IP")
            
            # 2. 为整个组测试所有可用IP，使用所有域名进行测试
            fastest_ips = await self.get_best_hosts(
                [group.domains[0]],  # 只需传入一个域名，因为只是用来测试IP
                # group.domains,  # 传入所有域名以获得更准确的延迟测试结果
                all_ips,
                self.max_latency
            )

            if not fastest_ips:
                logging.warning(f"组 {group.name} 未找到延迟满足要求的IP。")
                continue

            # 3. 将最快的IP应用到组内所有域名
            rprint(f"\n为组内所有域名应用发现的最快IP:")
            for domain in group.domains:
                new_entries = [f"{ip} {domain}" for ip, latency in fastest_ips]
                rprint(f"    {domain}:")
                # for entry in new_entries:
                #     rprint(f"      {entry}")
                all_entries.extend(new_entries)

        if all_entries:
            self.write_to_hosts_file(all_entries)
            rprint("\n[bold green]hosts文件更新完成！[/bold green]")
        else:
            rprint("\n[bold red]警告: 没有有效条目可写入。hosts文件未更新。[/bold red]")


# 域名组配置
DOMAIN_GROUPS = [
    DomainGroup(
        name="GitHub主站",
        domains=[
            "github.com",
        ],
        ips={
            "20.205.243.166",
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
        name="GitHub CDN",
        domains=[
            "raw.githubusercontent.com",
            "raw.github.com",
            "github-releases.githubusercontent.com",
            "objects.githubusercontent.com",
        ],
        ips={
            "185.199.108.133",
            "185.199.109.133",
            "185.199.110.133",
            "185.199.111.133",
        },
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


async def main():
    rprint("[bold]启动Hosts文件更新器...[/bold]")

    rprint("[bold]初始化SetHosts ...[/bold]")

    domain_ip_sets = SetHosts(domain_groups=DOMAIN_GROUPS)

    rprint("[bold]开始更新hosts文件...[/bold]")
    await domain_ip_sets.update_hosts()
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

    asyncio.run(main())
