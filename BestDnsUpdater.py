#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ctypes
import os
import sys
import threading
import dns.resolver
import platform
import subprocess
import logging
import argparse
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from prettytable import PrettyTable

# 设置日志记录
logger = logging.getLogger(__name__)

# 全局常量
DNS_TIMEOUT = 1  # DNS 查询超时时间（秒）
TEST_ITERATIONS = 2  # 每个DNS服务器测试的次数
DISPLAY_LIMIT = 20  # 每个域名显示的 DNS 服务器数量
MAX_THREADS = 20  # 最大线程数
BEST_DNS_NUM = 5  # 输出最佳 DNS 服务器数量

# 待测试的域名列表
DOMAINS_TO_TEST = [
    "translate.google.com",
    "translate.googleapis.com",
    "github.com",
    "tmdb.org",
    "api.github.com",
    "assets-cdn.github.com",
    "raw.githubusercontent.com",
]

# DNS服务器列表
DNS_SERVERS = {
    "全球": {
        "Google Public DNS": {
            "ipv4": ["8.8.8.8"],
            "ipv6": ["2001:4860:4860::8888"],
        },
        "Quad9": {
            "ipv4": ["9.9.9.9", "149.112.112.112"],
            "ipv6": ["2620:fe::fe", "2620:fe::9"],
        },
        "Verisign": {
            "ipv4": ["64.6.64.6", "64.6.65.6"],
            "ipv6": ["2620:74:1b::1:1", "2620:74:1c::2:2"],
        },
        "OpenDNS": {
            "ipv4": ["208.67.222.222"],
            "ipv6": ["2620:0:ccc::2"],
        },
    },
    "中国大陆": {
        "阿里云DNS": {
            "ipv4": ["223.5.5.5", "223.6.6.6"],
            "ipv6": ["2400:3200::1", "2400:3200:baba::1"],
        },
        "DNSPod (腾讯)": {"ipv4": ["119.29.29.29"], "ipv6": ["2402:4e00::"]},
        "114DNS": {
            "ipv4": ["114.114.114.114", "114.114.115.115"],
            "ipv6": ["240c::6666", "240c::6644"],
        },
    },
}


def test_dns_server(
    server: str, domain: str, record_type: str
) -> tuple[bool, float, list[str]]:
    """
    测试指定的DNS服务器

    :param server: DNS服务器IP地址
    :param domain: 要解析的域名
    :param record_type: DNS记录类型 (A 或 AAAA)
    :return: 元组 (是否成功解析, 响应时间, IP地址列表)
    """
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [server]
    resolver.lifetime = DNS_TIMEOUT
    start_time = time.time()
    try:
        answers = resolver.resolve(domain, record_type)
        end_time = time.time()
        response_time = (end_time - start_time) * 1000  # 转换为毫秒
        ips = [str(rdata) for rdata in answers]
        logger.debug(f"成功解析 {domain} 使用 {server} ({record_type}): {ips}")
        return True, response_time, ips
    except Exception as e:
        end_time = time.time()
        response_time = (end_time - start_time) * 1000  # 转换为毫秒
        logger.debug(f"无法解析 {domain} 使用 {server} ({record_type}): {str(e)}")
        return False, response_time, ["解析失败"]


def evaluate_dns_server(server: str, ip_version: str) -> tuple[float, float, dict]:
    """
    评估DNS服务器的性能

    :param server: DNS服务器IP地址
    :param ip_version: IP版本 ("ipv4" 或 "ipv6")
    :return: 元组 (成功率, 平均响应时间, 域名解析结果)
    """
    results = []
    resolutions = {}
    for domain in DOMAINS_TO_TEST:
        success, response_time, ips = test_dns_server(
            server, domain, "A" if ip_version == "ipv4" else "AAAA"
        )
        results.append((success, response_time))
        resolutions[domain] = ips

    success_rate = sum(1 for r in results if r[0]) / len(results)
    avg_response_time = statistics.mean(r[1] for r in results)
    return success_rate, avg_response_time, resolutions


def find_available_dns() -> tuple[dict, dict]:
    """
    查找最佳的DNS服务器并获取域名解析结果

    :return: 包含IPv4和IPv6最佳DNS服务器列表的字典，以及域名解析结果的字典
    """
    dns_performance = {}
    domain_resolutions = {domain: {} for domain in DOMAINS_TO_TEST}

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        future_to_server = {}
        for region, providers in DNS_SERVERS.items():
            for provider, servers in providers.items():
                for ip_version in ["ipv4", "ipv6"]:
                    for server in servers[ip_version]:
                        future = executor.submit(
                            evaluate_dns_server, server, ip_version
                        )
                        future_to_server[future] = (
                            server,
                            ip_version,
                            region,
                            provider,
                        )

        for future in as_completed(future_to_server):
            server, ip_version, region, provider = future_to_server[future]
            try:
                success_rate, avg_response_time, resolutions = future.result()
                dns_performance[server] = {
                    "success_rate": success_rate,
                    "avg_response_time": avg_response_time,
                    "ip_version": ip_version,
                    "region": region,
                    "provider": provider,
                }
                # 保存域名解析结果
                for domain, ips in resolutions.items():
                    domain_resolutions[domain][server] = ips

                logger.debug(
                    f"{ip_version.upper()} DNS {server} ({region} - {provider}) 成功率 {success_rate:.2%}, 平均延迟 {avg_response_time:.2f}ms"
                )
            except Exception as exc:
                logger.error(f"{server} 测试出错: {str(exc)}")

    # 对每个IP版本排序
    top_ipv4 = sorted(
        (s for s in dns_performance.items() if s[1]["ip_version"] == "ipv4"),
        key=lambda x: (-x[1]["success_rate"], x[1]["avg_response_time"]),
    )
    top_ipv6 = sorted(
        (s for s in dns_performance.items() if s[1]["ip_version"] == "ipv6"),
        key=lambda x: (-x[1]["success_rate"], x[1]["avg_response_time"]),
    )

    return {"ipv4": top_ipv4, "ipv6": top_ipv6}, domain_resolutions


def print_domain_resolutions(
    domain_resolutions: dict[str, dict[str, list[str]]], dns_performance: dict
):
    """
    打印域名解析结果表格

    :param domain_resolutions: 域名解析结果
    :param dns_performance: DNS性能数据
    """
    for domain, resolutions in domain_resolutions.items():
        table = PrettyTable()
        table.title = f"域名 {domain} 的解析结果"
        table.field_names = ["DNS服务器", "IP版本", "区域", "提供商", "解析结果"]
        table.align["DNS服务器"] = "l"
        table.align["解析结果"] = "l"

        ipv4_count = 0
        ipv6_count = 0

        for server, ips in resolutions.items():
            info = dns_performance[server]
            if info["ip_version"] == "ipv4" and ipv4_count < DISPLAY_LIMIT:
                table.add_row(
                    [
                        server,
                        info["ip_version"].upper(),
                        info["region"],
                        info["provider"],
                        "\n".join(ips[:3]) + ("\n..." if len(ips) > 3 else ""),
                    ]
                )
                ipv4_count += 1
            elif info["ip_version"] == "ipv6" and ipv6_count < DISPLAY_LIMIT:
                table.add_row(
                    [
                        server,
                        info["ip_version"].upper(),
                        info["region"],
                        info["provider"],
                        "\n".join(ips[:3]) + ("\n..." if len(ips) > 3 else ""),
                    ]
                )
                ipv6_count += 1

            if ipv4_count >= DISPLAY_LIMIT and ipv6_count >= DISPLAY_LIMIT:
                break

        print(table)
        print()  # 为了美观，在表格之间添加一个空行


def set_dns_servers(ipv4_dns_list: list[str], ipv6_dns_list: list[str]):
    """
    设置系统DNS服务器

    :param ipv4_dns_list: IPv4 DNS服务器列表
    :param ipv6_dns_list: IPv6 DNS服务器列表
    """
    system = platform.system()
    logger.info(f"正在设置DNS服务器for {system}")
    if system == "Windows":
        try:
            interfaces = subprocess.check_output(
                ["netsh", "interface", "show", "interface"]
            ).decode("gbk")
        except UnicodeDecodeError:
            interfaces = subprocess.check_output(
                ["netsh", "interface", "show", "interface"]
            ).decode("utf-8", errors="ignore")

        for line in interfaces.split("\n"):
            if "Connected" in line or "已连接" in line:
                interface = line.split()[-1]
                if ipv4_dns_list:
                    logger.debug(
                        f"设置IPv4 DNS for {interface}: {', '.join(ipv4_dns_list)}"
                    )
                    subprocess.run(
                        [
                            "netsh",
                            "interface",
                            "ipv4",
                            "set",
                            "dns",
                            interface,
                            "static",
                            ipv4_dns_list[0],
                        ]
                    )
                    for dns in ipv4_dns_list[1:]:
                        subprocess.run(
                            [
                                "netsh",
                                "interface",
                                "ipv4",
                                "add",
                                "dns",
                                interface,
                                dns,
                                "index=2",
                            ]
                        )
                if ipv6_dns_list:
                    logger.debug(
                        f"设置IPv6 DNS for {interface}: {', '.join(ipv6_dns_list)}"
                    )
                    subprocess.run(
                        [
                            "netsh",
                            "interface",
                            "ipv6",
                            "set",
                            "dns",
                            interface,
                            "static",
                            ipv6_dns_list[0],
                        ]
                    )
                    for dns in ipv6_dns_list[1:]:
                        subprocess.run(
                            [
                                "netsh",
                                "interface",
                                "ipv6",
                                "add",
                                "dns",
                                interface,
                                dns,
                                "index=2",
                            ]
                        )

    elif system == "Linux":
        with open("/etc/resolv.conf", "w") as f:
            for dns in ipv4_dns_list:
                logger.debug(f"添加IPv4 DNS到 /etc/resolv.conf: {dns}")
                f.write(f"nameserver {dns}\n")
            for dns in ipv6_dns_list:
                logger.debug(f"添加IPv6 DNS到 /etc/resolv.conf: {dns}")
                f.write(f"nameserver {dns}\n")
    elif system == "Darwin":  # macOS
        all_dns = ipv4_dns_list + ipv6_dns_list
        dns_string = " ".join(all_dns)
        logger.debug(f"设置DNS for Wi-Fi: {dns_string}")
        subprocess.run(["networksetup", "-setdnsservers", "Wi-Fi"] + all_dns)
    else:
        logger.error(f"不支持的操作系统: {system}")


def get_best_dns_by_region(dns_list: list, region: str) -> tuple[str, dict] | None:
    """
    根据区域获取最佳DNS服务器

    :param dns_list: DNS服务器列表
    :param region: 区域
    :return: 最佳DNS服务器信息或None
    """
    return next((s for s in dns_list if s[1]["region"] == region), None)


def get_best_dns_overall(dns_list: list) -> tuple[str, dict]:
    """
    获取整体最佳DNS服务器

    :param dns_list: DNS服务器列表
    :return: 可用DNS服务器信息
    """
    return max(
        dns_list,
        key=lambda x: (x[1]["success_rate"], -x[1]["avg_response_time"]),
    )


def get_recommended_dns(available_dns: dict, algorithm: str) -> dict[str, list]:
    """
    获取推荐的DNS服务器

    :param available_dns: 最佳DNS服务器列表
    :param algorithm: 推荐算法 ("region" 或 "overall")
    :return: 推荐的DNS服务器列表
    """
    recommended = {"ipv4": [], "ipv6": []}
    for ip_version in ["ipv4", "ipv6"]:
        if algorithm == "region":
            cn = get_best_dns_by_region(available_dns[ip_version], "中国大陆")
            global_ = get_best_dns_by_region(available_dns[ip_version], "全球")
            recommended[ip_version] = [
                cn[0] if cn else None,
                global_[0] if global_ else None,
            ]
        elif algorithm == "overall":
            best = get_best_dns_overall(available_dns[ip_version])
            # second_best = get_best_dns_overall(
            #     [dns for dns in available_dns[ip_version] if dns != best]
            # )
            # recommended[ip_version] = [best[0], second_best[0]]
            recommended[ip_version] = [best[0]]
    return recommended


def print_recommended_dns_table(dns_list: list, ip_version: str, available_dns: dict):
    """
    打印推荐的DNS服务器表格

    :param dns_list: 推荐的DNS服务器列表
    :param ip_version: IP版本 ("ipv4" 或 "ipv6")
    :param available_dns: 可用DNS服务器信息
    """
    table = PrettyTable()
    table.title = f"推荐的最佳{ip_version.upper()} DNS服务器"
    table.field_names = ["DNS", "提供商", "区域", "成功率", "平均延迟(ms)"]
    for dns in dns_list:
        if dns:
            # 在best_dns列表中查找正确的服务器信息
            server_info = next(
                (info for server, info in available_dns[ip_version] if server == dns),
                None,
            )
            if server_info:
                table.add_row(
                    [
                        dns,
                        server_info["provider"],
                        server_info["region"],
                        f"{server_info['success_rate']:.2%}",
                        f"{server_info['avg_response_time']:.2f}",
                    ]
                )
    print(table)
    print()  # 表格之间添加空行以美化


def print_available_dns(available_dns, best_dns_num):
    print()
    print(f"可用DNS服务器:")

    for ip_version in ["ipv4", "ipv6"]:
        if available_dns[ip_version]:
            # 使用PrettyTable展示前 n 个DNS服务器信息
            table = PrettyTable()
            table.title = f"前 {best_dns_num} 个可用 {ip_version.upper()} DNS服务器"
            table.field_names = [
                "排名",
                "服务器",
                "区域",
                "提供商",
                "成功率",
                "平均延迟(ms)",
            ]

            for i, (server, info) in enumerate(
                available_dns[ip_version][:best_dns_num], 1
            ):
                table.add_row(
                    [
                        i,
                        server,
                        info["region"],
                        info["provider"],
                        f"{info['success_rate']:.2%}",
                        f"{info['avg_response_time']:.2f}",
                    ]
                )
            print(table)
            print()


def get_input_with_timeout(prompt, timeout=10):
    print(prompt, end="", flush=True)
    user_input = []

    def input_thread():
        user_input.append(input())

    thread = threading.Thread(target=input_thread)
    thread.daemon = True
    thread.start()

    thread.join(timeout)
    if thread.is_alive():
        print("\n已超时，自动执行...")
        return "y"
    print()  # 换行
    return user_input[0].strip() if user_input else "y"


def main():
    """
    主函数
    """

    # 创建自定义的日志格式化器
    log_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%I:%M%p"
    )

    # 创建控制台日志处理器并设置格式化器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    # 设置日志级别
    logger = logging.getLogger()
    if args.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    # 添加控制台处理器到日志
    if not logger.handlers:
        logger.addHandler(console_handler)

    logger.info("开始测试DNS服务器...")
    available_dns, domain_resolutions = find_available_dns()

    if available_dns["ipv4"] or available_dns["ipv6"]:
        if args.show_resolutions:
            logger.info("显示域名解析结果...")
            dns_performance = {
                server: info
                for dns_list in available_dns.values()
                for server, info in dns_list
            }
            print_domain_resolutions(domain_resolutions, dns_performance)

        # 防止 best_dns_num 数值超过数组长度
        num_servers = min(len(available_dns["ipv4"]), len(available_dns["ipv6"]))
        if args.best_dns_num > num_servers:
            args.best_dns_num = num_servers

        if args.show_availbale_list:
            # 输出最好的前 best_dns_num 个dns服务器
            print_available_dns(available_dns, args.best_dns_num)

        print()
        logger.debug("推荐的最佳DNS服务器:")
        recommended_dns = get_recommended_dns(available_dns, args.algorithm)
        for ip_version in ["ipv4", "ipv6"]:
            if recommended_dns[ip_version]:
                print_recommended_dns_table(
                    recommended_dns[ip_version], ip_version, available_dns
                )

        confirm = get_input_with_timeout(
            "\n是否要设置系统DNS为推荐的最佳服务器？(y/n，10秒后自动执行): ", 10
        )
        if confirm.lower() == "y":
            set_dns_servers(recommended_dns["ipv4"], recommended_dns["ipv6"])
            logger.info("DNS服务器已更新")
        else:
            logger.info("操作已取消")
    else:
        logger.warning("未找到合适的DNS服务器")


def is_admin() -> bool:
    """
    检查当前用户是否具有管理员权限

    :return: 是否具有管理员权限
    """
    try:
        return os.getuid() == 0
    except AttributeError:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0


def run_as_admin():
    """
    以管理员权限重新运行脚本
    """
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
    parser = argparse.ArgumentParser(
        description="DNS解析器和设置工具,请使用管理员权限运行"
    )
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    parser.add_argument(
        "--show-availbale-list",
        "--list",
        action="store_true",
        help="显示可用dns列表，通过 --num 控制娴熟数量",
    )
    parser.add_argument(
        "--best-dns-num",
        "--num",
        default=BEST_DNS_NUM,
        type=int,
        action="store",
        help="显示最佳DNS服务器的数量",
    )
    parser.add_argument(
        "--algorithm",
        "--mode",
        choices=["region", "overall"],
        default="region",
        help="推荐最佳DNS的算法 (按区域或整体)",
    )
    parser.add_argument(
        "--show-resolutions",
        "--show",
        action="store_true",
        help="显示域名解析结果",
    )
    args = parser.parse_args()

    if not is_admin():
        logger.info("需要管理员权限来设置DNS服务器。正在尝试提升权限...")
        run_as_admin()

    main()
