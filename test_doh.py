import asyncio
import ssl
from typing import List

import dns.message
import dns.query
import dns.rdatatype
import httpx

# 常见的 DNS 服务列表，包括全球和中国的服务
DNS_SERVERS = {
    'google': {
        'type': 'udp',
        'server': '8.8.8.8',
        'port': 53
    },
    'cloudflare': {
        'type': 'udp',
        'server': '1.1.1.1',
        'port': 53
    },
    'quad9': {
        'type': 'udp',
        'server': '9.9.9.9',
        'port': 53
    },
    'aliyun': {
        'type': 'doh',
        'server': 'https://dns.alidns.com/dns-query',
    },
    'cloudflare_doh': {
        'type': 'doh',
        'server': 'https://1.1.1.1/dns-query',
    },
    '360': {
        'type': 'doh',
        'server': 'https://doh.360.cn/dns-query',
    },
    'google_doh': {
        'type': 'doh',
        'server': 'https://dns.google/resolve',
    },
    'aliyun_dot': {
        'type': 'dot',
        'server': 'tls://dns.alidns.com',
    }
}


# 通过 UDP 查询 DNS
def perform_udp_query(domain: str, server: str, port: int = 53) -> dict:
    try:
        # 创建 DNS 查询消息
        query = dns.message.make_query(domain, dns.rdatatype.A)
        response = dns.query.udp(query, server, port)
        answers = [str(answer) for answer in response.answer]
        return {'server': server, 'result': answers}
    except Exception as e:
        return {'server': server, 'error': str(e)}


# 通过 DoH 查询 DNS
async def perform_doh_query(domain: str, server: str) -> dict:
    try:
        url = f"{server}?name={domain}&type=A"
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                answers = [answer['data'] for answer in data.get('Answer', [])]
                return {'server': server, 'result': answers}
            else:
                return {'server': server, 'error': f"HTTP Error: {response.status_code}"}
    except Exception as e:
        return {'server': server, 'error': str(e)}


# 通过 DoT 查询 DNS
async def perform_dot_query(domain: str, server: str) -> dict:
    try:
        url = f"tls://{server}?name={domain}&type=A"
        # 使用 httpx 模拟 DoT 查询
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                answers = [answer['data'] for answer in data.get('Answer', [])]
                return {'server': server, 'result': answers}
            else:
                return {'server': server, 'error': f"HTTP Error: {response.status_code}"}
    except Exception as e:
        return {'server': server, 'error': str(e)}


# 执行查询并对比结果
async def compare_dns_results(domain: str, servers: List[str]):
    results = []
    for server_name in servers:
        server_info = DNS_SERVERS.get(server_name)
        if not server_info:
            results.append({'server': server_name, 'error': 'Invalid server'})
            continue
        if server_info['type'] == 'udp':
            result = perform_udp_query(domain, server_info['server'], server_info['port'])
        elif server_info['type'] == 'doh':
            result = await perform_doh_query(domain, server_info['server'])
        elif server_info['type'] == 'dot':
            result = await perform_dot_query(domain, server_info['server'])
        results.append(result)
    return results


# 调用函数并输出结果
async def main():
    domain = "translate.google.com"
    servers_to_test = ['google', 'cloudflare', 'quad9', 'aliyun', 'cloudflare_doh', '360', 'google_doh']
    results = await compare_dns_results(domain, servers_to_test)
    for result in results:
        print(result)


# 运行主函数
if __name__ == "__main__":
    asyncio.run(main())
