#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LibreTV 源列表自动构建器
=========================
在 GitHub Actions 上定时运行：拉取社区源目录 → 逐个实测「搜索能力」→
生成 LibreTV-SourceList JSON（sources.json），供 LibreTV 通过
DEFAULT_SUBSCRIPTIONS 订阅（24h 自动刷新）。

关键点：只保留「真的能搜到结果」的源，而不是「接口活着」的源。
—— 采集站会关掉 ac=videolist 搜索接口防滥用，只看存活会误判
   （实测：面板标"100% 在线"的 19 个源里只有 10 个真能搜）。

成人源处理：
  默认【保留】，只按要求打上 isAdult 标记 —— LibreTV 端开启成人过滤时会
  自动跳过带标记的源，关掉过滤就能用，两边都灵活。
  若要把产物放进公开仓库，设环境变量 FILTER_ADULT=1 改为直接剔除。
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

# 社区源目录（每 6 小时监测一次，暴露 JSON）
CATALOGS = [
    "https://mylazily.github.io/ziyuanzhan/data/online.json",
]

# 兜底候选（社区目录拉不到时使用）
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

# 成人源识别词 —— 同时匹配「源名称」和「API 域名」，用于打 isAdult 标记
ADULT_WORDS = [
    "🔞", "成人", "福利", "麻豆", "豆豆", "辣椒", "黑料", "桃花", "黄色仓库",
    "souav", "xxav", "xingba", "杏吧", "湿乐园", "番号", "fhapi", "av资源",
    "搜av", "鲨鱼", "细胞", "香蕉", "飘零", "色猫", "老色", "奥斯卡", "樱花",
    "jkun", "精品x", "danaizi", "大X子", "情色", "里番", "无码", "swag",
    "meimei", "91porn", "supjav", "美少女", "白嫖", "水机", "香X儿",
]

# 名称清理：去掉常见后缀，让界面清爽
NAME_SUFFIX = re.compile(r"(资源站|采集站|资源|影视站|影视)$")


def fetch(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read(600_000).decode("utf-8", "ignore")


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
        if FILTER_ADULT and adult:          # 仅在显式开启过滤时剔除
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


def main():
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

    alive.sort(key=lambda s: (s["isAdult"], s["name"]))   # 正常源排前面

    out = {
        "name": f"LibreTV 自动源列表（{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} 实测）",
        "version": 2,
        "sources": alive[:150],
        "liveSources": [],
    }
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)

    n_adult = sum(1 for s in alive if s["isAdult"])
    print(f"\n可用源 {len(alive)}/{len(cand)}（其中 isAdult 标记 {n_adult} 个）"
          f"  ->  已写入 {OUT}", file=sys.stderr)
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
