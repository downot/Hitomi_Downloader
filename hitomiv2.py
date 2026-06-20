import asyncio
import json
import os
import re
import tempfile
import time
import urllib.parse
import zipfile
from typing import IO, Callable, Optional, Awaitable, Any, cast
import httpx
from pydantic import BaseModel, Field, field_validator
from tqdm import tqdm
from setup_logger import getLogger, DEBUG_LEVEL, INFO_LEVEL
import uuid
import random
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.console import Console, Group as RichGroup
from rich.text import Text
from rich.table import Table
from collections import deque

logger, setLoggerLevel, _ = getLogger('Hitomi')

domain = 'ltn.gold-usergeneratedcontent.net'
galleryblockextension = '.html'
galleryblockdir = 'galleryblock'
nozomiextension = '.nozomi'

index_dir = 'tagindex'
galleries_index_dir = 'galleriesindex'
languages_index_dir = 'languagesindex'
nozomiurl_index_dir = 'nozomiurlindex'

index_versions = {
    index_dir: '',
    galleries_index_dir: '',
    languages_index_dir: '',
    nozomiurl_index_dir: ''
}

debug = False

proxy_var = (os.environ.get('http_proxy', None) or os.environ.get('HTTP_PROXY', None)
             or os.environ.get('HTTPS_PROXY', None) or os.environ.get('https_proxy', None))
proxy: Optional[httpx.Proxy] = None

if proxy_var:
    proxy = httpx.Proxy(proxy_var)
    logger.info(f'Using proxy: {proxy}')


def setProxy(http_proxy_url: str):
    global proxy
    proxy = httpx.Proxy(http_proxy_url)


def setDebug(target_state: bool = None):
    global debug
    if target_state is None:
        debug = not debug
    else:
        debug = target_state
    if debug:
        setLoggerLevel(DEBUG_LEVEL)
    else:
        setLoggerLevel(INFO_LEVEL)
    return debug


search_cache = {}


async def robustGet(client: httpx.AsyncClient, get_url: str, header=None):
    logger.debug(f'Request: {get_url}')
    for itime in range(10):
        try:
            response = await client.get(get_url, headers=header)
            if 200 <= response.status_code < 300:
                return response
            elif response.status_code == 404:
                return None
            elif response.status_code == 503:
                wait_time = random.uniform(2.0, 5.0) + (itime * 1.5)
                logger.warning(f'Server returned 503 (Rate limited), triggering safety wait for {wait_time:.1f}s...')
                await asyncio.sleep(wait_time)
                continue
            else:
                if itime > 2:
                    logger.warning(f'Server returned {response.status_code}, Attempt {itime}')
        except Exception as e:
            logger.warning(f'Request error: {type(e)}:{e}')
        await asyncio.sleep(0.5 * (itime + 1))
    return None


async def setGG(client: httpx.AsyncClient, add_timestamp=False):
    if add_timestamp:
        gg_url = f'https://ltn.gold-usergeneratedcontent.net/gg.js?_={int(time.time() * 1000)}'
    else:
        gg_url = 'https://ltn.gold-usergeneratedcontent.net/gg.js?'
    gg_resp = (await robustGet(client, gg_url)).text
    m = {}
    keys = []
    for match in re.finditer(
            r"case\s+(\d+):(?:\s*o\s*=\s*(\d+))?", gg_resp):
        key, value = match.groups()
        keys.append(int(key))
        if value:
            value = int(value)
            for key in keys:
                m[key] = value
            keys.clear()
    for match in re.finditer(
            r"if\s+\(g\s*===?\s*(\d+)\)[\s{]*o\s*=\s*(\d+)", gg_resp):
        m[int(match.group(1))] = int(match.group(2))
    d = re.search(r"(?:var\s|default:)\s*o\s*=\s*(\d+)", gg_resp)
    b = re.search(r"b:\s*[\"'](.+)[\"']", gg_resp)
    return m, b.group(1).strip("/"), int(d.group(1)) if d else 0


class Language(BaseModel):
    name: str
    galleryid: int
    language_localname: str
    url: str


class Parody(BaseModel):
    # 保留原始字段名 parody，即使它是单数形式
    parody: str
    url: str


class Group(BaseModel):
    group: str
    url: str


class Tag(BaseModel):
    tag: str
    url: str
    male: Optional[str] = ""
    female: Optional[str] = ""

    @field_validator('male', 'female', mode='before')
    @classmethod
    def coerce_int_to_str(cls, v):
        """
        拦截原始输入：若为 int 则强转为 str，
        解决 tags.x.female 报错 [input_value=1, input_type=int]
        """
        if isinstance(v, int):
            return str(v)
        return v


class PageInfo(BaseModel):
    hasavif: int
    hash: str
    height: int
    width: int
    name: str


class Character(BaseModel):
    character: str
    url: str


class Artist(BaseModel):
    artist: str
    url: str


# --- 主模型定义 ---

class Comic(BaseModel):
    id: str  # 原始 JSON 中 id 为字符串类型
    title: str
    type: str
    language: str
    language_localname: str
    date: str

    galleryurl: str
    blocked: int
    # 嵌套结构：Pydantic 会自动处理 list[Model] 的转换
    files: list[PageInfo]
    languages: list[Language]
    # 初始化可选
    parodys: list[Parody] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    artists: list[Artist] = Field(default_factory=list)
    # 可选字段 (Nullable)
    datepublished: Optional[str] = None
    related: Optional[list[int]] = None
    groups: Optional[list[Group]] = None
    videofilename: Optional[str] = None
    japanese_title: Optional[str] = None
    video: Optional[str] = None
    # 这里的 list[Any] 用于处理空列表或未知结构的列表
    scene_indexes: list[Any] = Field(default_factory=list)

    @field_validator('parodys', mode='before')
    @classmethod
    def prevent_parodys_none(cls, v):
        if v is None:
            return []
        return v

    @field_validator('tags', mode='before')
    @classmethod
    def prevent_tags_none(cls, v):
        if v is None:
            return []
        return v

    @field_validator('characters', mode='before')
    @classmethod
    def prevent_characters_none(cls, v):
        if v is None:
            return []
        return v

    @field_validator('artists', mode='before')
    @classmethod
    def prevent_artists_none(cls, v):
        if v is None:
            return []
        return v


    # 针对 id 的预处理验证器
    @field_validator('id', mode='before')
    @classmethod
    def coerce_id_to_str(cls, v):
        """
        拦截原始输入：若为 int 则强转为 str，
        解决 id 报错 [input_value=1441484, input_type=int]
        """
        if isinstance(v, int):
            return str(v)
        return v


async def decodeDownloadUrls(files: list[PageInfo]) -> dict[str, str]:
    logger.info(f"Decoding download URLs for {len(files)} files")
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=5)
    async with httpx.AsyncClient(
            proxy=proxy,
            timeout=5,
            limits=limits,
            verify=False,  # 如果为了极致速度且信任环境，可关闭 verify (可选)
            http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
    ) as client:
        gg_m, gg_b, gg_d = await setGG(client)

    # noinspection PyUnusedLocal
    def url2hash(galleryid, image: PageInfo, ext=None):
        # 修改点 1: image['name'] -> image.name
        # 注意：保留了原代码中的逻辑（虽然 'or image.name...' 这部分永远不会执行）
        ext = ext or "webp" or image.name.split('.').pop()
        # 修改点 2: image["hash"] -> image.hash
        ihash = image.hash
        # 核心逻辑保持不变
        inum = int(ihash[-1] + ihash[-3:-1], 16)
        url = "https://{}{}.{}/{}/{}/{}.{}".format(
            ext[0],
            gg_m.get(inum, gg_d) + 1,
            "gold-usergeneratedcontent.net",
            gg_b,
            inum,
            ihash,
            ext,
        )
        return url

    download_urls = {}
    for file in files:
        # 修改点 3: file['name'] -> file.name
        image_name = re.sub(r'\.[^.]+$', '.webp', file.name)
        # 传入 Pydantic 对象 file
        download_urls[image_name] = url2hash(0, file, None)
    logger.debug(f"Successfully decoded {len(download_urls)} links")
    return download_urls


async def refreshVersion():
    for version_name, version in index_versions.items():
        if version_name == index_dir:
            continue
        url = f'https://{domain}/{version_name}/version?_={int(time.time() * 1000)}'
        logger.debug(f'请求url: {url}')
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=20)
        async with httpx.AsyncClient(
                proxy=proxy,
                timeout=20,
                limits=limits,
                verify=False,  # 如果为了极致速度且信任环境，可关闭 verify (可选)
                http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
        ) as client:
            response = await robustGet(client, url)
        version = response.text
        if not version:
            logger.error(f'refresh_versions: getting {version_name} failed')
        else:
            logger.debug(f'{version_name}:{version}')
            index_versions[version_name] = version
            break
        if version == '':
            raise ConnectionError(f'{version_name} failed totally')


async def getComic(gallery_id) -> Optional[Comic]:
    req_url = f'https://{domain}/galleries/{gallery_id}.js'
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=20)
    async with httpx.AsyncClient(
            proxy=proxy,
            timeout=20,
            limits=limits,
            verify=False,  # 如果为了极致速度且信任环境，可关闭 verify (可选)
            http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
    ) as client:
        response = await robustGet(client, req_url)
    if response is None:
        return None
    # 使用正则表达式匹配 galleryinfo 变量的 JSON 对象
    if 'galleryinfo' not in response.text:
        logger.error(response.text)
        raise ValueError("galleryinfo not found")
    match = re.search(r'{.*', response.text, re.DOTALL)
    # 提取匹配的 JSON 字符串
    json_str = match.group(0)
    # 解析 JSON 字符串为 Python 字典
    try:
        galleryinfo_dict = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding JSON: {e}")
    comic = Comic.model_validate(galleryinfo_dict)
    logger.info(f"Successfully fetched Comic info: [{comic.id}] {comic.title}")
    return comic


async def downloadComic(comic: Comic, file: IO[bytes],
                        max_threads=5,
                        phase_callback: Callable[[str], Awaitable[None]] = None,
                        enable_tempfile=True) -> bool:
    logger.info(f"Starting Comic download: [{comic.id}] {comic.title}")
    if not comic.files:
        logger.warning(f'comic has no files')
        return False
    headers = {'referer': 'https://hitomi.la' + urllib.parse.quote(comic.galleryurl)}
    pbar: Optional[tqdm] = None
    file_urls = await decodeDownloadUrls(comic.files)
    if phase_callback is None:
        # 如果没有传入 callback，我们仍然需要一个进度条
        pbar = tqdm(total=len(file_urls), desc=f"Downloading {comic.id}", unit="file", leave=False)

    # noinspection PyUnusedLocal
    async def _tqdm_callback(dl_url: str):
        if pbar:
            pbar.update(1)

    if phase_callback is None:
        phase_callback = _tqdm_callback
    sem = asyncio.Semaphore(max_threads)

    async def download_file(_sem: asyncio.Semaphore,
                            client: httpx.AsyncClient,
                            file_url: str,
                            dl_file: IO[bytes]) -> bool:
        async with _sem:
            response = await robustGet(client, file_url, header=headers)
            dl_file.write(response.content)
            await phase_callback(file_url)
            return True

    limits = httpx.Limits(max_keepalive_connections=max_threads, max_connections=max_threads)
    fp_list: dict[str, tempfile.SpooledTemporaryFile] = {}
    try:
        async with httpx.AsyncClient(
                proxy=proxy,
                timeout=5,
                limits=limits
        ) as client_o:
            if enable_tempfile:
                for name, url in file_urls.items():
                    fp_list[name] = tempfile.SpooledTemporaryFile(max_size=1024 ** 2)
            else:
                for name, url in file_urls.items():
                    fp_list[name] = tempfile.SpooledTemporaryFile(max_size=0)
            tasks = []
            for name, fp in fp_list.items():
                tasks.append(download_file(sem, client_o, file_urls[name], fp))
            downloaded_results = cast(
                list[bool],
                cast(object, await asyncio.gather(*tasks))
            )


        # 哈希级可复现构建, 勿修改任何打包流程
        with zipfile.ZipFile(file, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for name, file_data in fp_list.items():
                zinfo = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                zinfo.external_attr = 0o100644 << 16
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                file_data.seek(0)
                zipf.writestr(zinfo, file_data.read())
        file.seek(0)
    finally:
        for fp in fp_list.values():
            fp.close()
    success = all(downloaded_results)
    logger.info(f"Comic [{comic.id}] 下载完成, 状态: {'成功' if success else '失败'}")
    return success


import struct
import hashlib

# ================= 核心配置与工具函数 =================

# B-Tree 分支因子 (Hitomi 默认为 16)
B = 16


async def get_bytes(client: httpx.AsyncClient, url: str, start: int, length: int) -> bytes:
    """基于 robustGet 的 Range 请求封装"""
    end = start + length - 1
    headers = {'Range': f'bytes={start}-{end}', 'Referer': 'https://hitomi.la/'}
    logger.debug(f'正在向 {url} 请求 {start} 到 {end} 的数据')
    resp = await robustGet(client, f"https://{domain}/{url}", header=headers)
    if resp and resp.status_code in [200, 206]:
        return resp.content
    return b''


def hash_term(term: str) -> bytes:
    """计算搜索词的 SHA-256 哈希（前4字节）"""
    sha = hashlib.sha256()
    sha.update(term.encode('utf-8'))
    return sha.digest()[:4]


# ================= B-Tree 与 索引解析类 =================

class BTreeNode:
    def __init__(self, data: bytes):
        self.keys: list[bytes] = []
        self.datas: list[tuple[int, int]] = []  # (offset, length)
        self.subnode_addrs: list[int] = []
        self._parse(data)

    def _parse(self, data: bytes):
        view = memoryview(data)
        pos = 0
        # 1. 解析 Keys
        num_keys = struct.unpack('>i', view[pos:pos + 4])[0]
        pos += 4
        for _ in range(num_keys):
            key_size = struct.unpack('>i', view[pos:pos + 4])[0]
            pos += 4
            key = view[pos:pos + key_size].tobytes()
            self.keys.append(key)
            pos += key_size
        # 2. 解析 Datas (Offset/Length)
        num_datas = struct.unpack('>i', view[pos:pos + 4])[0]
        pos += 4
        for _ in range(num_datas):
            offset = struct.unpack('>Q', view[pos:pos + 8])[0]
            pos += 8
            length = struct.unpack('>i', view[pos:pos + 4])[0]
            pos += 4
            self.datas.append((offset, length))
        # 3. 解析子节点地址
        num_subnodes = B + 1
        for _ in range(num_subnodes):
            addr = struct.unpack('>Q', view[pos:pos + 8])[0]
            pos += 8
            self.subnode_addrs.append(addr)


async def b_search_recursive(client: httpx.AsyncClient, key: bytes, node_addr: int = 0) -> Optional[tuple[int, int]]:
    """递归遍历远程 B-Tree"""
    version = index_versions[galleries_index_dir]
    index_url = f"{galleries_index_dir}/galleries.{version}.index"
    logger.debug(f'对 key: {key} node_addr: {node_addr} 执行b树搜索')
    # 读取节点头 (4KB 通常足够包含一个节点)
    node_data = await get_bytes(client, index_url, node_addr, 4096)
    if not node_data:
        return None
    node = BTreeNode(node_data)
    # 比较 Key
    idx = 0
    found = False
    for i, k in enumerate(node.keys):
        if key < k:
            idx = i
            break
        elif key == k:
            idx = i
            found = True
            break
        else:
            idx = i + 1
    if found:
        return node.datas[idx]
    # 如果是叶子节点且没找到
    if all(addr == 0 for addr in node.subnode_addrs):
        return None
    sub_addr = node.subnode_addrs[idx]
    if sub_addr == 0:
        return None
    return await b_search_recursive(client, key, sub_addr)


async def get_ids_from_data(client: httpx.AsyncClient, offset: int, length: int) -> set[int]:
    """从 .data 文件读取 ID 列表"""
    logger.debug(f'正在获取 offset: {offset}, length: {length} 的数据')
    version = index_versions[galleries_index_dir]
    data_url = f"{galleries_index_dir}/galleries.{version}.data"
    raw_data = await get_bytes(client, data_url, offset, length)
    if not raw_data:
        return set()
    # 解析 int32 数组: [count, id1, id2, ...]
    count = struct.unpack('>i', raw_data[0:4])[0]
    ids = set()
    for i in range(count):
        start = 4 + i * 4
        gid = struct.unpack('>i', raw_data[start: start + 4])[0]
        ids.add(gid)
    return ids


async def get_ids_from_nozomi(client: httpx.AsyncClient, subpath: str) -> set[int]:
    """解析 .nozomi 文件 (纯 ID 列表)"""
    logger.debug(f'对 {subpath} 发起 nozomi 请求')
    url = f"{subpath}.nozomi"
    # 请求头中需要设置正确的 Referer，否则可能 403
    headers = {'Referer': 'https://hitomi.la/'}
    resp = await robustGet(client, f"https://{domain}/{url}", header=headers)
    if not resp or resp.status_code != 200:
        return set()
    data = resp.content
    total_ids = len(data) // 4
    ids = set()
    for i in range(total_ids):
        gid = struct.unpack('>i', data[i * 4: (i + 1) * 4])[0]
        ids.add(gid)
    return ids


# ================= 搜索逻辑 =================

async def search_single_term(client: httpx.AsyncClient, term: str) -> set[int]:
    """处理单个搜索词（包含 Tag 映射逻辑）"""
    term = term.replace('_', ' ')
    # 1. 处理命名空间 Tag (例如: female:big_breasts)
    if ':' in term:
        logger.debug(f'处理命名空间 Tag: {term}')
        left, right = term.split(':', 1)
        # 根据 search.js 的 nozomi 映射规则
        if left in ['female', 'male']:
            return await get_ids_from_nozomi(client, f"tag/{left}-{right}-all")
        elif left == 'language':
            return await get_ids_from_nozomi(client, f"index-{right}")
        elif left in ['artist', 'character', 'series', 'group']:
            return await get_ids_from_nozomi(client, f"{left}/{right}-all")
        elif left == 'type':  # e.g. type:manga
            return await get_ids_from_nozomi(client, f"type/{right}-all")
    # 2. 普通文本搜索 (B-Tree)
    logger.debug(f'处理单词: {term}')
    key = hash_term(term)
    data_ptr = await b_search_recursive(client, key, 0)
    if data_ptr:
        offset, length = data_ptr
        return await get_ids_from_data(client, offset, length)
    logger.debug(f'单词 {term} 未检索到任何结果')
    return set()


async def searchIDs(query: str, max_threads: int = 5) -> list[int]:
    """
        Main search entry point (Full parallel optimized version)
        """
    logger.info(f"Search: {query}")
    terms = query.lower().strip().split()
    positive_terms = []
    negative_terms = []
    or_groups = [[]]
    # 1. 词法解析
    for i, term in enumerate(terms):
        if term == 'or':
            continue
        is_prev_or = (i > 0 and terms[i - 1] == 'or')
        is_next_or = (i + 1 < len(terms) and terms[i + 1] == 'or')
        if is_prev_or or is_next_or:
            or_groups[-1].append(term)
            if not is_next_or:
                or_groups.append([])
            continue
        if term.startswith('-'):
            negative_terms.append(term[1:])
        else:
            positive_terms.append(term)
    or_groups = [g for g in or_groups if g]
    # 注意：在并行模式下，"将带冒号的 term 提到最前" 的排序不再影响网络请求顺序，
    # 但仍有助于后续集合运算时的某种微小确定性，故保留。
    positive_terms.sort(key=lambda x: 0 if ':' in x else 1)
    current_ids = set()
    first_round = True
    # ================= 执行搜索逻辑 (全并行化) =================
    # 1. 构建所有并行任务 (Tasks Construction)
    # 我们将 OR 组的处理、AND 词的处理、NOT 词的处理全部放入任务池
    # 1.1 OR 组任务
    # 每个 OR 组内部是并行的，组与组之间我们也希望并行获取数据
    or_tasks = []
    limits = httpx.Limits(max_keepalive_connections=max_threads, max_connections=max_threads)
    async with httpx.AsyncClient(
            proxy=proxy,
            timeout=5,
            limits=limits,
            verify=False,  # 如果为了极致速度且信任环境，可关闭 verify (可选)
            http2=True  # 如果服务器支持 HTTP/2，速度会起飞 (可选，需安装 httpx[http2])
    ) as client:
        for group in or_groups:
            # 对每个组创建一个 gather 任务
            or_tasks.append(asyncio.gather(*[search_single_term(client, t) for t in group]))
        # 1.2 AND 词任务
        and_tasks = [search_single_term(client, t) for t in positive_terms]
        # 1.3 NOT 词任务
        not_tasks = [search_single_term(client, t) for t in negative_terms]
        # ================= 等待数据返回 (Await I/O) =================
        # 这里我们分阶段 await，以便于逻辑处理，但 request 已经在此时可以并发发出
        # 若追求极致，可以使用 asyncio.gather 将所有 task 一起发出，但这会使结果处理逻辑变得复杂
        # 鉴于 or_groups 较少见，我们优先并行化 and_tasks
        # A. 处理 OR 组 (如果存在)
        if or_tasks:
            # group_results_list 是一个列表，每个元素是该组内所有 term 的结果列表
            group_results_list = await asyncio.gather(*or_tasks)
            for group_results in group_results_list:
                # 组内取并集 (Union)
                group_union = set()
                for res in group_results:
                    group_union.update(res)
                # 组间取交集 (Intersection)
                if first_round:
                    current_ids = group_union
                    first_round = False
                else:
                    current_ids.intersection_update(group_union)
        # B. 处理 AND 词 (正向筛选)
        if and_tasks:
            # === 关键修改：此处通过 gather 并行执行所有 AND 词的搜索 ===
            and_results = await asyncio.gather(*and_tasks)
            for res in and_results:
                if first_round:
                    current_ids = res
                    first_round = False
                else:
                    # 剪枝：如果已经为空，就没必要继续交集运算了
                    if not current_ids:
                        break
                    current_ids.intersection_update(res)
        # C. 处理 NOT 词 (负向筛选)
        if not_tasks and (current_ids or first_round):
            # 注意：如果 current_ids 为空且 first_round 为 True (即只有排除词)，
            # 逻辑上应该返回全集减去排除词。但 Hitomi 默认行为通常是不给全集的。
            # 这里维持原逻辑：只在有结果时进行排除。
            not_results = await asyncio.gather(*not_tasks)
            for res in not_results:
                if current_ids:
                    current_ids.difference_update(res)
    # 排序结果 (ID 越大越新)
    result = sorted(list(current_ids), reverse=True)
    logger.info(f"Search complete, found {len(result)} results")
    return result


class TaskStatus:
    def __init__(self, comic_id: int):
        self.comic_id = comic_id
        self.title = str(comic_id)
        self.total = 0
        self.completed = 0
        self.state = "Waiting"
        self.msg = ""
        self.logs = deque(maxlen=5)
        self.worker_idx = None
        self.countdown_total = 0
        self.countdown_current = 0

    def log(self, message: str):
        self.logs.append(message)

    def update(self, completed: int = None, msg: str = None, state: str = None):
        if completed is not None:
            self.completed = completed
        if msg is not None:
            self.msg = msg
        if state is not None:
            self.state = state


class TUIManager:
    def __init__(self, comic_ids: list[int]):
        self.comic_ids = comic_ids
        self.tasks = {cid: TaskStatus(cid) for cid in comic_ids}
        self.global_logs = deque(maxlen=15)
        self.console = Console()
        self.layout = Layout()
        self.setup_layout()

    def get_task(self, comic_id: int) -> TaskStatus:
        return self.tasks[comic_id]

    def setup_layout(self):
        self.layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=10),
        )
        self.layout["body"].split_row(
            Layout(name="left_panel", ratio=1),
            Layout(name="right_panel", ratio=2),
        )

    def log(self, message: str):
        self.global_logs.append(message)

    def make_header(self) -> Panel:
        completed = sum(1 for t in self.tasks.values() if t.state in ("Completed", "Failed"))
        total = len(self.comic_ids)
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            Text.from_markup(f"[bold blue]Hitomi Downloader[/] [white]v2.0[/]"),
            Text.from_markup(f"[bold green]Total Progress: {completed}/{total}[/]"),
        )
        return Panel(grid, style="white on blue")

    def make_queue_panel(self) -> Panel:
        waiting = [str(t.comic_id) for t in self.tasks.values() if t.state == "Waiting"]
        completed = [str(t.comic_id) + ("(Success)" if t.state == "Completed" else "(Failure)") for t in self.tasks.values() if t.state in ("Completed", "Failed")]
        
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column("Waiting", justify="left", ratio=1)
        grid.add_column("Completed", justify="left", ratio=1)
        
        waiting_content = []
        if waiting:
            waiting_content.append(Text(f"Upcoming ({len(waiting)}):", style="bold yellow"))
            waiting_content.append(Text("\n".join(waiting[:20]) + ("\n..." if len(waiting) > 20 else "")))
        else:
            waiting_content.append(Text("None", style="dim"))
            
        completed_content = []
        if completed:
            completed_content.append(Text(f"Finished ({len(completed)}):", style="bold green"))
            completed_content.append(Text("\n".join(completed[-20:])))
        else:
            completed_content.append(Text("None", style="dim"))
            
        grid.add_row(RichGroup(*waiting_content), RichGroup(*completed_content))
            
        return Panel(grid, title="[bold]Task Queue", border_style="cyan")

    def make_active_panel(self) -> Panel:
        active_tasks = [t for t in self.tasks.values() if t.state not in ("Waiting", "Completed", "Failed")]
        
        if not active_tasks:
            return Panel(Text("Idle", justify="center", style="dim"), title="[bold green]Active Tasks", border_style="green")
            
        panels = []
        for t in active_tasks:
            if t.state == "Resting" and t.countdown_total > 0:
                progress_pct = ((t.countdown_total - t.countdown_current) / t.countdown_total * 100)
                bar_width = 30
                filled = int(bar_width * progress_pct / 100)
                bar = "█" * filled + "░" * (bar_width - filled)
                prog_text = f"[{bar}] Remaining {t.countdown_current}s"
            else:
                progress_pct = (t.completed / t.total * 100) if t.total > 0 else 0
                bar_width = 30
                filled = int(bar_width * progress_pct / 100)
                bar = "█" * filled + "░" * (bar_width - filled)
                prog_text = f"[{bar}] {progress_pct:.1f}% ({t.completed}/{t.total})"
            
            worker_tag = f"[Worker {t.worker_idx}] " if t.worker_idx is not None else ""
            content = [
                Text.from_markup(f"[bold magenta]{worker_tag}[/][bold yellow]ID: {t.comic_id}[/] | {t.title[:25]}"),
                Text(f"Status: {t.state} - {t.msg}"),
                Text(f"Progress: {prog_text}"),
            ]
            if t.logs:
                content.append(Text("\nLatest Logs:", style="bold cyan"))
                for log in list(t.logs)[-2:]:
                    content.append(Text(f"› {log}", style="dim", overflow="ellipsis"))
            
            panels.append(Panel(RichGroup(*content), border_style="blue"))
            
        return Panel(RichGroup(*panels), title="[bold blue]Active Tasks", border_style="blue")

    def make_footer(self) -> Panel:
        log_text = Text()
        for log in list(self.global_logs):
            log_text.append("› ", style="dim")
            log_text.append(Text.from_ansi(log.strip()))
            log_text.append("\n")
        return Panel(log_text, title="System Logs", border_style="magenta")

    def update_layout(self):
        self.layout["header"].update(self.make_header())
        self.layout["left_panel"].update(self.make_queue_panel())
        self.layout["right_panel"].update(self.make_active_panel())
        self.layout["footer"].update(self.make_footer())


import logging

class TUIHandler(logging.Handler):
    def __init__(self, tui: TUIManager):
        super().__init__()
        self.tui = tui

    def emit(self, record):
        try:
            msg = self.format(record)
            self.tui.log(msg)
        except Exception:
            self.handleError(record)


async def cliDownload(comic_list: list[int], output_dir: str = '.', concurrency: int = 1):
    await refreshVersion()
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    tui = TUIManager(comic_list)
    
    # 设置日志重定向
    tui_handler = TUIHandler(tui)
    tui_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(tui_handler)
    # 完全禁用控制台输出，避免任何警告或错误打断 TUI
    setLoggerLevel(999)

    sem = asyncio.Semaphore(concurrency)
    worker_pool = asyncio.Queue()
    for i in range(concurrency):
        worker_pool.put_nowait(i)

    async def download_task(comic_id: int, task_index: int):
        worker_idx = await worker_pool.get()
        async with sem:
            status = tui.get_task(comic_id)
            status.worker_idx = worker_idx + 1
            try:
                status.update(0, "Fetching info...", state="In Progress")
                comic = await getComic(comic_id)
                if not comic:
                    status.log("Fetch failed")
                    status.update(msg="Fetch failed", state="Failed")
                    return

                status.title = comic.title
                status.total = len(comic.files)
                status.update(0, "Decoding URLs...")

                # 过滤非法字符
                safe_title = re.sub(r'[\\/:*?"<>|]', '_', comic.title)
                save_path = os.path.join(output_dir, f'{safe_title}.zip')

                async def progress_cb(url: str):
                    status.completed += 1

                status.update(0, "Downloading...")
                with open(save_path, 'wb') as f:
                    success = await downloadComic(comic, f, max_threads=5, phase_callback=progress_cb)

                if success:
                    if task_index < len(comic_list) - 1:
                        sleep_time = random.randint(10, 30)
                        status.countdown_total = sleep_time
                        for s in range(sleep_time, 0, -1):
                            status.countdown_current = s
                            status.update(msg=f"Next task soon", state="Resting")
                            await asyncio.sleep(1)
                    status.update(status.total, "Completed", state="Completed")
                else:
                    status.log("Download failed")
                    status.update(msg="Download failed", state="Failed")
            except Exception as e:
                logger.error(f"Task exception: {e}")
                status.update(msg=f"Exception: {e}", state="Failed")
            finally:
                status.worker_idx = None
                worker_pool.put_nowait(worker_idx)

    async def ui_updater():
        while True:
            tui.update_layout()
            await asyncio.sleep(0.1)

    try:
        tui.update_layout()
        with Live(tui.layout, console=tui.console, refresh_per_second=10, screen=True):
            ui_task = asyncio.create_task(ui_updater())
            tasks = [download_task(comic_id, i) for i, comic_id in enumerate(comic_list)]
            await asyncio.gather(*tasks)
            ui_task.cancel()
            tui.update_layout() # Final render
    finally:
        # 恢复日志级别
        setLoggerLevel(logging.INFO)
        logger.removeHandler(tui_handler)


async def cliSearch(search_string: str):
    print(await searchIDs(search_string))


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Hitomi CLI')
    parser.add_argument('-p', '--proxy',
                        dest='proxy',
                        type=str,
                        help='Set proxy')
    arg_group = parser.add_mutually_exclusive_group(required=True)
    arg_group.add_argument('-d', '--download',
                           dest="comic_ids",
                           type=int,
                           nargs='+',
                           help='Download comic')
    arg_group.add_argument('-s', '--search',
                           dest='search_str',
                           type=str,
                           help='Search comic')
    parser.add_argument('-o', '--output',
                        dest='output_dir',
                        type=str,
                        default='../../Downloads/',
                        help='Set storage path (default: current dir)')
    parser.add_argument('-c', '--concurrency',
                        dest='concurrency',
                        type=int,
                        default=1,
                        help='Set concurrency (default: 1)')
    args = parser.parse_args()
    if args.proxy:
        logger.info(f'Using proxy: {args.proxy}')
        setProxy(args.proxy)
    asyncio.run(refreshVersion())
    if args.comic_ids:
        asyncio.run(cliDownload(args.comic_ids, args.output_dir, args.concurrency))
    else:
        asyncio.run(cliSearch(args.search_str))
