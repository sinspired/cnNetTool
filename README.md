# cnNetTool

[![Release Version](https://img.shields.io/github/v/release/sinspired/cnNetTool?display_name=tag&logo=github&label=Release)](https://github.com/sinspired/cnNetTool/releases/latest)
[![GitHub repo size](https://img.shields.io/github/repo-size/sinspired/cnNetTool?logo=github)
](https://github.com/sinspired/cnNetTool)
[![GitHub last commit](https://img.shields.io/github/last-commit/sinspired/cnNetTool?logo=github&label=最后提交：)](ttps://github.com/sinspired/cnNetTool)

基于Python的网络小工具。

针对某些区域DNS污染问题，自动筛选解析速度最快的 DNS 服务器、自动设置hosts文件（包括tinyManager刮削源tmdb.org、themoviedb.org，Google/chrome网页翻译等），支持Windows、Linux、MacOS。

## 运行

# 安装

首先安装 python，然后在终端中运行以下命令：

```bash
git clone https://github.com/sinspired/cnNetTool.git
cd cnNetTool
pip install -r requirements.txt
```
这将安装所有依赖项

# 参数说明

**CloudflareBestIP** 可以接受以下参数：

### DNS 服务器工具 `BestDnsUpdater.py`

* --debug 启用调试日志
* --show-availbale-list, --list 显示可用dns列表，通过 --num 控制娴熟数量
* --best-dns-num BEST_DNS_NUM, --num 显示最佳DNS服务器的数量
* --algorithm --mode {region,overall} 默认 `region` 平衡IPv4和ipv6 DNS
* --show-resolutions, --show 显示域名解析结果

### Hosts文件工具 `BestHostsUpdater.py`

* --log-level 设置日志输出等级，'DEBUG', 'INFO', 'WARNING', 'ERROR'
* --num-fastest 设置选择的最快IP数量
* --max-latency 设置允许的最大延迟（毫秒）

命令行键入 `-h` `help` 获取帮助

`py BestDnsUpdater.py -h`

`py BestHostsUpdater.py -h`

# 运行

请使用管理员权限，在项目目录运行，分别设置解析最快的DNS服务器，更新hosts文件。 **接受传递参数，大部分时候直接运行即可**。

```bash
py BestDnsUpdater.py 
py BestHostsUpdater.py
```

# 最新发行版下载

Windows下载可执行文件双击运行即可，注意使用管理员权限。linux系统使用sudo。

或在命令行设置参数运行：

```pwsh
./BestDnsUpdater.exe --best-dns-num 10
./BestHostsUpdater.exe --num-fastest 3 --max-latency 500 
```

[![Release Detail](https://img.shields.io/github/v/release/sinspired/cnNetTool?sort=date&display_name=release&logo=github&label=Release)](https://github.com/sinspired/cnNetTool/releases/latest)