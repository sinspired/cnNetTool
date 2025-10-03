# cnNetTool

[![Release Version](https://img.shields.io/github/v/release/sinspired/cnNetTool?display_name=tag&logo=github&label=Release)](https://github.com/sinspired/cnNetTool/releases/latest)
[![GitHub repo size](https://img.shields.io/github/repo-size/sinspired/cnNetTool?logo=github)
](https://github.com/sinspired/cnNetTool)
[![GitHub last commit](https://img.shields.io/github/last-commit/sinspired/cnNetTool?logo=github&label=最后提交：)](ttps://github.com/sinspired/cnNetTool)

全面解锁Github，解决加载慢、无法访问等问题！解锁Google翻译，支持chrome网页翻译及插件，解锁划词翻译，以及依赖Google翻译API的各种平台插件。解锁tinyMediaManager影视刮削。

自动设置最佳DNS服务器。

> 适合部分地区饱受dns污染困扰，访问 GitHub 卡顿、抽风、图裂，无法使用Chrome浏览器 自带翻译功能，无法刮削影视封面等问题。分别使用 `setDNS` 自动查找最快服务器并设置，使用 `setHosts` 自动查找DNS映射主机并设置。支持Windows、Linux、MacOS。Enjoy!❤

> [!NOTE]
> 首次运行大约需要2分钟以获取DNS主机并建立缓存，请耐心等待。后续运行速度大概二三十秒。

## 一、使用方法

### 1.1 自动操作

直接下载下方文件，解压后双击运行，enjoy❤！

[![Release Detail](https://img.shields.io/github/v/release/sinspired/cnNetTool?sort=date&display_name=release&logo=github&label=Release)](https://github.com/sinspired/cnNetTool/releases/latest)

程序使用DNS服务器实时解析和DNS A、AAAA记录获取IPv4及IPv6地址，通过本地网络环境检测延迟并进行SSL证书验证。

由于需要进行 `hosts` 修改备份操作，exe文件已标记需要管理员权限，如果被系统误报病毒，请允许后再次操作。

> 强烈建议采用本方法，如果喜欢折腾，可以继续往下看。

### 1.2 手动操作

#### 1.2.1 复制下面的内容

```bash

# cnNetTool Start in 2025-10-03 23:25:38 +08:00
140.82.112.26	alive.github.com
140.82.114.25	live.github.com
140.82.116.5	api.github.com
140.82.116.9	codeload.github.com
140.82.112.21	central.github.com
140.82.116.4	gist.github.com
140.82.116.4	github.com
140.82.112.17	github.community
151.101.65.194	github.global.ssl.fastly.net
16.15.180.59	github-com.s3.amazonaws.com
16.15.201.117	github-production-release-asset-2e65be.s3.amazonaws.com
52.217.232.217	github-production-user-asset-6210df.s3.amazonaws.com
52.217.203.161	github-production-repository-file-5c1aeb.s3.amazonaws.com
13.107.42.16	pipelines.actions.githubusercontent.com
185.199.108.154	github.githubassets.com
3.5.28.111		github-cloud.s3.amazonaws.com
192.0.66.2		github.blog
185.199.111.153	githubstatus.com
185.199.111.153	assets-cdn.github.com
185.199.111.153	github.io
140.82.114.22	collector.github.com
140.82.114.22	education.github.com
185.199.109.133	avatars.githubusercontent.com
185.199.109.133	avatars0.githubusercontent.com
185.199.109.133	avatars1.githubusercontent.com
185.199.109.133	avatars2.githubusercontent.com
185.199.109.133	avatars3.githubusercontent.com
185.199.109.133	avatars4.githubusercontent.com
185.199.109.133	avatars5.githubusercontent.com
185.199.109.133	camo.githubusercontent.com
185.199.109.133	cloud.githubusercontent.com
185.199.109.133	desktop.githubusercontent.com
185.199.109.133	favicons.githubusercontent.com
185.199.109.133	github.map.fastly.net
185.199.109.133	media.githubusercontent.com
185.199.109.133	objects.githubusercontent.com
185.199.109.133	private-user-images.githubusercontent.com
185.199.109.133	raw.githubusercontent.com
185.199.109.133	user-images.githubusercontent.com
18.238.192.8	tmdb.org
18.238.192.8	api.tmdb.org
18.238.192.8	files.tmdb.org
18.238.192.8	themoviedb.org
18.238.192.8	api.themoviedb.org
18.238.192.8	www.themoviedb.org
18.238.192.8	auth.themoviedb.org
143.244.50.85	image.tmdb.org
143.244.50.85	images.tmdb.org
18.155.186.154	imdb.com
18.155.186.154	www.imdb.com
18.155.186.154	secure.imdb.com
18.155.186.154	s.media-imdb.com
18.155.186.154	us.dd.imdb.com
18.155.186.154	www.imdb.to
18.155.186.154	imdb-webservice.amazon.com
18.155.186.154	origin-www.imdb.com
18.238.192.45	m.media-amazon.com
18.238.192.45	Images-na.ssl-images-amazon.com
18.238.192.45	images-fe.ssl-images-amazon.com
18.238.192.45	images-eu.ssl-images-amazon.com
18.238.192.45	ia.media-imdb.com
18.238.192.45	f.media-amazon.com
18.238.192.45	imdb-video.media-imdb.com
18.238.192.45	dqpnq362acqdi.cloudfront.net
142.250.68.42	translate.google.com
142.250.68.42	translate.googleapis.com
142.250.68.42	translate-pa.googleapis.com
142.250.68.42	jnn-pa.googleapis.com
18.155.192.87	plugins.jetbrains.com
18.155.192.87	download.jetbrains.com
18.155.192.87	cache-redirector.jetbrains.com

# Update time: 2025-10-03 23:25:38 +08:00
# GitHub仓库: https://github.com/sinspired/cnNetTool
# cnNetTool End

```

以上内容会自动定时更新， 数据更新时间：2025-10-03 23:25:38 +08:00

> [!NOTE]
> 由于数据获取于非本地网络环境，请自行测试可用性，否则请采用方法 1，使用本地网络环境自动设置。

#### 1.2.2 修改 hosts 文件

hosts 文件在每个系统的位置不一，详情如下：
- Windows 系统：`C:\Windows\System32\drivers\etc\hosts`
- Linux 系统：`/etc/hosts`
- Mac（苹果电脑）系统：`/etc/hosts`
- Android（安卓）系统：`/system/etc/hosts`
- iPhone（iOS）系统：`/etc/hosts`

修改方法，把第一步的内容复制到文本末尾：

1. Windows 使用记事本。
2. Linux、Mac 使用 Root 权限：`sudo vi /etc/hosts`。
3. iPhone、iPad 须越狱、Android 必须要 root。

> [!NOTE]
> Windows系统可能需要先把 `hosts` 文件复制到其他目录，修改后再复制回去，否则可能没有修改权限。

## 二、安装

首先安装 python，然后在终端中运行以下命令：

```bash
git clone https://github.com/sinspired/cnNetTool.git
cd cnNetTool
pip install -r requirements.txt
```
这将安装所有依赖项

## 参数说明

**cnNetTool** 可以接受以下参数：

### DNS 服务器工具 `SetDNS.py`

* --debug 启用调试日志
* --show-availbale-list, --list, -l 显示可用dns列表，通过 --num 控制显示数量
* --best-dns-num BEST_DNS_NUM, --num, -n 显示最佳DNS服务器的数量
* --algorithm --mode {region,overall} 默认 `region` 平衡IPv4和ipv6 DNS，选择 `overall` 则会在所有IP中选择最快IP
* --show-resolutions, --resolutions, -r 显示域名解析结果
* --only-global, --global 仅使用国际DNS服务器

### Hosts文件工具 `SetHosts.py`

* -log 设置日志输出等级，'DEBUG', 'INFO', 'WARNING', 'ERROR'
* -num --num-fastest 限定Hosts主机 ip 数量
* -max --max-latency 设置允许的最大延迟（毫秒）
* -v --verbose 打印运行信息

命令行键入 `-h` `help` 获取帮助

`py SetDNS.py -h`

`py SetHosts.py -h`

## 三、运行

请使用管理员权限，在项目目录运行，分别设置解析最快的DNS服务器，更新hosts文件。 **接受传递参数，大部分时候直接运行即可**。

```bash
py SetDNS.py 
py SetHosts.py
```
可执行文件也可带参数运行
```pwsh
./SetDNS.exe --best-dns-num 10 --mode 'overall' --show-resolutions
./SetHosts.exe --num-fastest 3 --max-latency 500 
```

