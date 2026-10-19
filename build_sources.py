#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LibreTV 源列表自动构建器（点播源 + 直播源）
=============================================
在 GitHub Actions 上定时运行：
  ① 拉取社区源目录 → 逐个实测「搜索能力」→ 生成点播源列表
  ② 拉取 iptv-org 直播源 → 合并去重 → 生成 live.m3u 镜像
  ③ 合成一份 LibreTV-SourceList JSON，点播源与直播源一起下发

LibreTV 通过 DEFAULT_SUBSCRIPTIONS 订阅（24h 自动刷新），因此
NAS 端无需任何改动 —— 换源、加直播源全在云端完成。

关键点：点播源只保留「真的能搜到结果」的，而不是「接口活着」的。
—— 采集站会关掉 ac=videolist 搜索接口防滥用，只看存活会误判
   （实测：面板标"100% 在线"的 19 个源里只有 10 个真能搜）。

成人源处理：
  默认【保留】，只按要求打上 isAdult 标记 —— LibreTV 端开启成人过滤时会
  自动跳过带标记的源，关掉过滤就能用，两边都灵活。
  若要把产物放进公开仓库，设环境变量 FILTER_ADULT=1 改为直接剔除。

直播源说明：
  原始 iptv-org 源在国内可达（GitHub Pages，实测 0.4~0.7s），但频道有重复、
  分组混乱。这里合并去重后输出成 live.m3u，由 CI 一并推到 Gitee，
  NAS 从 Gitee 拉（国内更快更稳）。epg 用 51zmt（实测 100 频道节目单）。
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

TIMEOUT = int(os.environ.get("PROBE_TIMEOUT", "12"))
KEY = os.environ.get("PROBE_KEY", "流浪地球2")
WORKERS = int(os.environ.get("PROBE_WORKERS", "16"))
OUT = os.environ.get("OUT_FILE", "sources.json")

# "1" = 直接剔除成人源；默认 "0" = 保留并打 isAdult 标记
FILTER_ADULT = os.environ.get("FILTER_ADULT", "0") == "1"

# ─────────────────── 点播源（Apple CMS 采集站）───────────────────

CATALOGS = [
    "https://mylazily.github.io/ziyuanzhan/data/online.json",
]

FALLBACK = [
    ("暴风", "https://bfzyapi.com/api.php/provide/vod/"),
    ("非凡", "https://ffzy5.tv/api.php/provide/vod/"),
    ("极速", "https://jszyapi.com/api.php/provide/vod/"),
    ("虎牙", "https://huyazy.net/api.php/provide/vod/"),
    ("豪华", "https://haohuazy.com/api.php/provide/vod/"),
    ("金鹰", "https://jyzyapi.com/provide/vod/"),
    ("360", "https://360zy.com/api.php/provide/vod/"),
    ("猫眼", "https://api.maoyanapi.top/api.php/provide/vod"),
    ("速播", "https://subocj.com/api.php/provide/vod/"),
    ("光速", "https://api.guangsuapi.com/api.php/provide/vod/"),
]

ADULT_WORDS = [
    "🔞", "成人", "福利", "麻豆", "豆豆", "辣椒", "黑料", "桃花", "黄色仓库",
    "souav", "xxav", "xingba", "杏吧", "湿乐园", "番号", "fhapi", "av资源",
    "搜av", "鲨鱼", "细胞", "香蕉", "飘零", "色猫", "老色", "奥斯卡", "樱花",
    "jkun", "精品x", "danaizi", "大X子", "情色", "里番", "无码", "swag",
    "meimei", "91porn", "supjav", "美少女", "白嫖", "水机", "香X儿",
]

NAME_SUFFIX = re.compile(r"(资源站|采集站|资源|影视站|影视)$")

# ─────────────────── 直播源（IPTV / M3U）───────────────────

# (显示名, M3U 地址) —— 国内源优先（频道质量高、国内可播率高）
# 注意：这些都在 raw.githubusercontent.com 上，国内直连拉不动，
# 所以由 Actions（海外）拉取后镜像到 Gitee 再分发给 NAS。
LIVE_SOURCES = [
    ("Guovin 自动测速", "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/result.m3u"),
    ("best-fan 全部", "https://raw.githubusercontent.com/best-fan/iptv-sources/main/cn_all.m3u8"),
    ("suxuang 典藏版", "https://raw.githubusercontent.com/suxuang/myIPTV/main/ipv4.m3u"),
    ("YanG-1989 聚合", "https://raw.githubusercontent.com/YanG-1989/m3u/main/Gather.m3u"),
    ("akiralereal 自托管", "https://raw.githubusercontent.com/akiralereal/iptv/main/IPTV.m3u"),
    # iptv-org 兜底（GitHub Pages，国内直连可达，不分时段）
    ("iptv-org 中文", "https://iptv-org.github.io/iptv/languages/zho.m3u"),
]

# raw 源在国内拉不动，走镜像兜底；Actions 在海外直连即可（第一顺位就是直连）
LIVE_MIRRORS = ["https://ghproxy.net/", "https://gh-proxy.com/"]

LIVE_EPG = os.environ.get("LIVE_EPG", "http://epg.51zmt.top:8000/e.xml")
LIVE_OUT = os.environ.get("LIVE_OUT_FILE", "live.m3u")
SKIP_LIVE = os.environ.get("SKIP_LIVE", "0") == "1"

# 镜像后的地址（CI 里设成 Gitee 上的 raw 地址；本地跑可不设）
LIVE_MIRROR_URL = os.environ.get("LIVE_MIRROR_URL", "").strip()

# M3U 里明显播不了的标记，直接剔除
LIVE_DROP_MARKS = ["[Geo-blocked]", "[Not 24/7]", "[Offline]", "[Dead]"]

# 分组名中文化（iptv-org 原始分组是英文）
GROUP_ZH = {
    "General": "综合", "News": "新闻", "Movies": "电影", "Sports": "体育",
    "Kids": "少儿", "Entertainment": "娱乐", "Lifestyle": "生活",
    "Education": "教育", "Culture": "文化", "Business": "财经",
    "Religious": "宗教", "Music": "音乐", "Documentary": "纪录",
    "Shop": "购物", "Undefined": "其他", "Family": "家庭",
    "Travel": "旅游", "Food": "美食", "Auto": "汽车", "Science": "科学",
    "Weather": "天气", "Comedy": "喜剧", "Series": "剧集",
    "Animation": "动画", "Outdoor": "户外", "Relax": "休闲",
    "Legislative": "政务", "Classic": "经典", "Local": "地方",
}

# 频道名里的画质/状态后缀，显示时去掉
NAME_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:\d{3,4}[piPI]|HD|SD|FHD|UHD|4K|8K|Not 24/7|Geo-blocked)\s*[\)\]]")

# ── 频道精简 ───────────────────────────────────────────────
# iptv-org 是全球社区收集的，杂台极多（宗教/购物/境外小台）。
# 实测国内能播的也就 2/3，与其让用户在大杂烩里翻，不如只留真会看的。
# 保留白名单：命中即保留（CCTV / 卫视 / 主要地方台 / 港台主力 / 央视系）
LIVE_KEEP = re.compile(
    r"CCTV|央视|卫视|TVB|Jade|明珠|凤凰|Phoenix|TVBS|中天|东森|三立|民视|华视|台视|"
    r"湖南|浙江|江苏|东方|北京|上海|广东|深圳|山东|安徽|河南|湖北|四川|重庆|天津|"
    r"辽宁|黑龙江|吉林|河北|山西|陕西|甘肃|宁夏|青海|新疆|西藏|云南|贵州|广西|海南|"
    r"福建|江西|内蒙古|CGTN|纪录|电影|戏曲|少儿|卡酷|金鹰|CHC|SiTV|Chengdu|Shenzhen|"
    r"NewTV|BesTV|Hunan|Zhejiang|Jiangsu|Dragon|CGTN|Phoenix",
    re.I)

# 直接丢弃的分组
LIVE_DROP_GROUPS = {"宗教", "购物", "Religious", "Shop", "Adult"}

# 是否启用精简（LIVE_SLIM=0 可关掉，拉全量）
LIVE_SLIM = os.environ.get("LIVE_SLIM", "1") == "1"


def fetch(url, timeout=TIMEOUT, limit=600_000):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read(limit).decode("utf-8", "ignore")


def fetch_live(url, limit=8_000_000):
    """拉直播源：先直连（Actions 在海外，raw 直连可达），
    失败则依次走镜像（本地/国内环境用得上）。"""
    last = None
    for prefix in [""] + LIVE_MIRRORS:
        try:
            body = fetch(prefix + url, timeout=60, limit=limit)
            if body.lstrip().startswith("#EXTM3U"):
                return body
            last = ValueError("返回内容不是 M3U")
        except Exception as e:
            last = e
    raise last if last else RuntimeError("拉取失败")


def looks_adult(*parts):
    blob = " ".join(p or "" for p in parts).lower()
    return any(w.lower() in blob for w in ADULT_WORDS)


def is_cms_api(api):
    return "api.php/provide/vod" in api or "provide/vod" in api


def clean_name(name):
    return NAME_SUFFIX.sub("", name.strip()) or name.strip()


def load_candidates():
    """返回 [(name, api, is_adult)] —— 社区目录优先，兜底补充，按 API 地址去重"""
    seen, cand = set(), []

    def add(name, api, desc=""):
        api = (api or "").strip()
        if not api or api in seen or not is_cms_api(api):
            return
        adult = looks_adult(name, desc, api)
        if FILTER_ADULT and adult:
            return
        seen.add(api)
        cand.append((clean_name(name), api, adult))

    for url in CATALOGS:
        try:
            data = json.loads(fetch(url))
            for item in data.get("data", []):
                add(item.get("name") or "", item.get("api") or "",
                    item.get("description") or "")
            print(f"[目录] {url} -> 累计 {len(cand)} 个候选（已按地址去重）",
                  file=sys.stderr)
        except Exception as e:
            print(f"[目录] {url} 拉取失败: {type(e).__name__}", file=sys.stderr)

    for n, u in FALLBACK:
        add(n, u)

    return cand


def probe(item):
    """返回 (name, api, is_adult, status, hits, note)；status: ok/nosearch/dead"""
    name, api, adult = item
    url = f"{api}?ac=videolist&wd={urllib.parse.quote(KEY)}"
    referer = f"https://{api.split('//')[1].split('/')[0]}/"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            body = r.read(400_000).decode("utf-8", "ignore")
        dt = round(time.time() - t0, 2)
        try:
            data = json.loads(body)
        except ValueError:
            if "暂不支持" in body or "不支持搜索" in body:
                return name, api, adult, "nosearch", 0, dt
            return name, api, adult, "dead", 0, dt
        n = len(data.get("list") or [])
        return name, api, adult, ("ok" if n else "nosearch"), n, dt
    except Exception as e:
        return name, api, adult, "dead", 0, type(e).__name__


# ─────────────────── 直播源构建 ───────────────────

def parse_m3u(txt):
    """解析 M3U → [(name, url, attrs)]"""
    out = []
    for blk in re.split(r"\n(?=#EXTINF)", txt):
        m = re.match(r"#EXTINF:\s*([^,]*),(.*)", blk.strip())
        u = re.search(r"^(https?://\S+)", blk, re.M)
        if m and u:
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
            out.append((m.group(2).strip(), u.group(1).strip(), attrs))
    return out


def build_live():
    """拉取并合并直播源，输出去重后的 live.m3u；返回频道数"""
    seen, chans = set(), []
    for label, url in LIVE_SOURCES:
        try:
            txt = fetch_live(url)
            parsed = parse_m3u(txt)
            added = dropped = 0
            for name, u, attrs in parsed:
                if any(mk in name for mk in LIVE_DROP_MARKS):
                    dropped += 1
                    continue
                if LIVE_SLIM:
                    grp_raw = (attrs.get("group-title") or "").split(";")[0].strip()
                    if grp_raw in LIVE_DROP_GROUPS:
                        dropped += 1
                        continue
                    if not LIVE_KEEP.search(name):
                        dropped += 1
                        continue
                if u in seen:
                    continue
                seen.add(u)
                chans.append((name, u, attrs, label))
                added += 1
            print(f"[直播] {label}: 解析 {len(parsed)}，新增 {added}，剔除标记 {dropped}",
                  file=sys.stderr)
        except Exception as e:
            print(f"[直播] {label} 拉取失败: {type(e).__name__}", file=sys.stderr)

    if not chans:
        return 0

    lines = ["#EXTM3U"]
    for name, u, attrs, label in chans:
        raw_grp = (attrs.get("group-title") or "").split(";")[0].strip()
        grp = GROUP_ZH.get(raw_grp, raw_grp) or label
        logo = attrs.get("tvg-logo", "")
        tvgid = attrs.get("tvg-id", "")
        nm = NAME_NOISE.sub("", name).strip() or name
        nm = nm.replace('"', "'")
        lines.append(
            f'#EXTINF:-1 tvg-id="{tvgid}" tvg-logo="{logo}" '
            f'group-title="{grp}",{nm}')
        lines.append(u)

    with open(LIVE_OUT, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")

    print(f"[直播] 合并去重后 {len(chans)} 个频道 -> {LIVE_OUT}", file=sys.stderr)
    return len(chans)


def main():
    # ① 点播源
    cand = load_candidates()
    mode = "开（直接剔除）" if FILTER_ADULT else "关（保留并打标记）"
    print(f"候选源: {len(cand)} 个 | 成人源过滤: {mode} | "
          f"并发 {WORKERS} …", file=sys.stderr)

    alive = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(probe, it): it for it in cand}
        for f in as_completed(futs):
            name, api, adult, status, hits, note = f.result()
            mark = {"ok": "OK  ", "nosearch": "NOSRCH", "dead": "DEAD "}[status]
            tag = " [18+]" if adult else ""
            print(f"  [{mark}] {name:<14}{tag:<6} {hits:>3} 条  {note}  {api}",
                  file=sys.stderr)
            if status == "ok":
                alive.append({"name": name, "url": api, "isAdult": bool(adult)})

    alive.sort(key=lambda s: (s["isAdult"], s["name"]))

    # ② 直播源
    n_live = 0
    if not SKIP_LIVE:
        n_live = build_live()

    live_list = []
    if n_live:
        if LIVE_MIRROR_URL:
            live_list = [{
                "name": "IPTV 直播（自动镜像·点开即看）",
                "url": LIVE_MIRROR_URL,
                "epg": LIVE_EPG,
            }]
        else:
            for label, url in LIVE_SOURCES:
                live_list.append({"name": label, "url": url, "epg": LIVE_EPG})

    # ③ 合成订阅
    out = {
        "name": f"LibreTV 自动源列表（{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} 实测）",
        "version": 3,
        "sources": alive[:100],
        "liveSources": live_list[:50],
    }
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)

    n_adult = sum(1 for s in alive if s["isAdult"])
    print(f"\n点播源 {len(alive)}/{len(cand)}（isAdult {n_adult} 个）"
          f" | 直播源 {len(live_list)} 个（{n_live} 频道）"
          f"  ->  已写入 {OUT}", file=sys.stderr)
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
