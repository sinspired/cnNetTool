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
PING_TIMEOUT = 1  # ping 超时时间
NUM_FASTEST = 2  # 限定最快 ip 数量
MAX_LATENCY = 300  # 允许的最大延迟

# 初始化日志模块
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def parse_args():
    parser = argparse.ArgumentParser(description="Hosts Updater Script")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="设置日志输出等级，'DEBUG', 'INFO', 'WARNING', 'ERROR'",
    )
    parser.add_argument(
        "--num-fastest",
        default=NUM_FASTEST,
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


class HostsUpdater:
    def __init__(
        self,
        domain_sets: List[List[str]],
        ip_sets: List[Set[str]],
        num_fastest: int,
        max_latency: int,
        custom_dns_servers: List[str] = None,
    ):
        self.domain_sets = domain_sets
        self.ip_sets = ip_sets
        self.hosts_file_path = self.get_hosts_file_path()
        self.num_fastest = num_fastest
        self.max_latency = max_latency
        self.dns_servers = custom_dns_servers or [
            "2402:4e00::",  # DNSPod (IPv6)
            "8.8.8.8",  # Google Public DNS (IPv4)
            "2001:4860:4860::8888",  # Google Public DNS (IPv6)
            # ... (其他DNS服务器)
        ]

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

    async def test_ip_connection(
        self, ip: str, port: int = 443, timeout: float = PING_TIMEOUT
    ) -> float:
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
            response_time = (end - start) * 1000
            return response_time if response_time > 0 else float("inf")
        except Exception as e:
            logging.debug(f"连接测试失败 {ip}: {e}")
            return float("inf")

    async def ping_ip(self, ip: str) -> Tuple[str, float]:
        try:
            if self.is_ipv6(ip):
                response_time_ms = await self.test_ip_connection(ip)
            else:
                result = await asyncio.to_thread(ping, ip, timeout=PING_TIMEOUT)
                response_time_ms = result * 1000 if result is not None else float("inf")

            # Consider response times of 0 ms as invalid
            if response_time_ms == 0:
                logging.error(f"{ip} 响应时间为 0 ms，视为无效")
                return ip, float("inf")

            if response_time_ms != float("inf"):
                logging.debug(f"{ip} 响应时间: {response_time_ms:.2f} ms")
            else:
                logging.debug(f"{ip} 无响应")

            return ip, response_time_ms
        except Exception as e:
            logging.debug(f"ping {ip} 时出错: {e}")
            return ip, float("inf")

    async def get_fastest_ips(
        self, domains: List[str], file_ips: Set[str], max_latency: int
    ) -> List[Tuple[str, float]]:
        all_ips = set()
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

        rprint(f"[bold green]找到 {len(all_ips)} 个唯一IP地址[/bold green]")

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

        valid_results = [result for result in results if result[1] < max_latency]
        if not valid_results:
            logging.warning(f"未找到延迟小于 {max_latency}ms 的IP。")
            return []

        ipv4_results = [r for r in valid_results if not self.is_ipv6(r[0])]
        ipv6_results = [r for r in valid_results if self.is_ipv6(r[0])]

        fastest = []
        if ipv4_results and ipv6_results:
            fastest.append(min(ipv4_results, key=lambda x: x[1]))
            fastest.append(min(ipv6_results, key=lambda x: x[1]))
        else:
            fastest = sorted(valid_results, key=lambda x: x[1])[: self.num_fastest]

        rprint(
            f"[bold yellow]最快的 IP 地址(如有IPv6，优先添加了IPv6) (延迟 < {max_latency}ms):[/bold yellow]"
        )
        for ip, time in fastest:
            rprint(f"  [green]{ip}[/green]: [yellow]{time:.2f} ms[/yellow]")
        return fastest

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

            if line.startswith("# 以下条目由 HostsUpdater 脚本添加于"):
                skip = True

            if skip:
                if line == "" or line.startswith("#"):
                    continue
                else:
                    skip = False

            if (
                line.startswith("#") or not line
            ) and "以下条目由 HostsUpdater 脚本添加" not in line:
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
            f"# 以下条目由 HostsUpdater 脚本添加于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        rprint("\n[bold yellow]正在更新hosts文件...[/bold yellow]")
        for entry in new_entries:
            new_content.append(f"{entry} #mkhosts")
            rprint(f"添加条目:{entry}")

        with open(self.hosts_file_path, "w") as f:
            f.write("\n".join(new_content))

    async def update_hosts(self):
        all_entries = []
        for i, (domains, file_ips) in enumerate(zip(self.domain_sets, self.ip_sets), 1):
            rprint(f"\n[bold]正在处理第 {i} 组域名和IP...[/bold]")
            fastest_ips = await self.get_fastest_ips(
                domains, file_ips, args.max_latency
            )
            if fastest_ips:
                for domain in domains:
                    all_entries.extend([f"{ip}\t{domain}" for ip, _ in fastest_ips])
            else:
                logging.warning(f"第 {i} 组未找到有效IP。跳过这些域名。")

        if all_entries:
            self.write_to_hosts_file(all_entries)
            rprint("\n[bold green]hosts文件更新完成！[/bold green]")
        else:
            rprint("\n[bold red]警告: 没有有效条目可写入。hosts文件未更新。[/bold red]")


async def main():
    rprint("[bold]启动Hosts文件更新器...[/bold]")

    # tinyManager相关刮削资源
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

    # TMDB封面图
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

    # Google 翻译相关域名集合
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
        "74.125.204.139",
    }

    # GitHub
    domain_names_set4 = [
        "github.com",
        "api.github.com",
        "raw.githubusercontent.com",
        "raw.github.com",
        "github-releases.githubusercontent.com",
        "objects.githubusercontent.com",
    ]
    ip_addresses_set4 = {
        "20.205.243.166",
        "20.200.245.247",
        "140.82.114.4",
    }

    # Jetbrain
    domain_names_set5 = [
        "plugins.jetbrains.com",
        "download.jetbrains.com",
        "cache-redirector.jetbrains.com",
    ]
    ip_addresses_set5 = {
        "133.33.5.36",  # jetbrain
        "18.65.166.20",
        "52.84.251.69",
        "2a00:1450:4001:803::201a",
        "35.210.233.33",
        "74.125.204.139",
    }

    # dl.google
    domain_names_set6 = [
        "dl.google.com",
    ]
    ip_addresses_set6 = {
        "120.253.255.161",  # dl.google
        "180.163.151.33",
        "142.250.196.110",
    }

    rprint("[bold]初始化HostsUpdater...[/bold]")
    updater = HostsUpdater(
        domain_sets=[
            domain_names_set1,  # TMM
            domain_names_set2,  # TMDB image
            domain_names_set3,  # Google/chrome 翻译
            # domain_names_set4,  # GitHub
            # domain_names_set5,  # Jetbrain
            # domain_names_set6,  # dl.google
        ],
        ip_sets=[
            ip_addresses_set1,  # TMM
            ip_addresses_set2,  # TMDB image
            ip_addresses_set3,  # Google/chrome 翻译
            # ip_addresses_set4,  # GitHub
            # ip_addresses_set5,  # Jetbrain
            # ip_addresses_set6,  # dl.google
        ],
        num_fastest=args.num_fastest,
        max_latency=args.max_latency,
    )

    rprint("[bold]开始更新hosts文件...[/bold]")
    await updater.update_hosts()
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
