# cnNetTool
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

# 运行

在项目目录运行，分别设置解析最快的DNS服务器，更新hosts文件。

```bsh
py BestDnsUpdater.py 
py BestHostsUpdater.py
```
