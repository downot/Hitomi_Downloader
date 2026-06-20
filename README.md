# 2.0更新🎇

# Hitomi.la Downloader

[English](https://github.com/ACBAD/Hitomi_Downloader/blob/master/README_EN.md)

hitomi.la上漫画的搜索和下载的python实现

jm的漫画后边有广告，eh的漫画需要点数才能下，这个网站允许游客下载，但是看了看目前GitHub上好像还没有这个的逆向，于是花了点时间写了这个

基于逆向该网站客户端的js代码

## 欢迎提issue和pr

## 特点

- 支持代理
- 重试机制
- **多任务并行下载**：支持同时启动多个下载任务，提高下载效率
- **随机休息时间**：任务之间自动休息 10-30 秒，降低封号风险
- **批量下载**：支持一次性输入多个 ID 进行下载

直接运行 `hitomiv2.py` 即可进行搜索或下载。

### 1. 下载 Comic

支持同时下载多个 ID，并可设置并发数：

```bash
# 下载单个 ID
python hitomiv2.py -d 123456

# 下载多个 ID，并设置并发数为 3
python hitomiv2.py -d 123456 789012 345678 -c 3

# 设置输出目录
python hitomiv2.py -d 123456 -o ./my_comics
```

### 2. 搜索 Comic

```bash
# 搜索关键词
python hitomiv2.py -s "HayaseYuuka"
```

### 参数说明

- `-d`, `--download`: 下载指定的 Comic ID，支持多个 ID（空格分隔）。
- `-s`, `--search`: 搜索关键词。
- `-c`, `--concurrency`: 并发下载任务数，默认为 1（即顺序下载）。
- `-o`, `--output`: 设置下载文件的存储路径。
- `-p`, `--proxy`: 设置代理地址（例如 `http://127.0.0.1:10809`）。

### Chrome 扩展

打开Chrome开发模式，将 `hitomi-copy-ext` 目录拖放到 [chrome://extensions/](chrome://extensions/) 即可开启ID复制器，可以达到一键复制到 `python hitomiv2.py -d` 后面的参数。

## 注意事项

1. 关于初始化
   由于该网站具有反爬机制，因此需要获取一些参数用于解析。初始化的本质就是请求一些参数存储在本地，以防请求次数过多封禁ip，所以如果抛出没捕获的异常导致脚本停止运行也不会产生问题

    