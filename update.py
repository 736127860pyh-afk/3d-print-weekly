#!/usr/bin/env python3
"""
3D 打印前线 · 自动更新脚本
用法:
  python3 update.py              # 更新全部
  python3 update.py --news       # 仅更新新闻
  python3 update.py --trending   # 仅更新 GitHub 趋势

依赖: requests, beautifulsoup4
  pip3 install requests beautifulsoup4
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def save(name, payload):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  saved {path}  ({len(payload.get('items', []))} items)")

# ============================================================
# 1. 抓取 3D 打印新闻（Google News RSS，最近 7 天）
# ============================================================
FEEDS = [
    ("https://news.google.com/rss/search?q=%223D+printing%22+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen", "国际资讯"),
    ("https://news.google.com/rss/search?q=3D%E6%89%93%E5%8D%B0+when%3A7d&hl=zh-CN&gl=CN&ceid=CN%3Azh-Hans", "中文资讯"),
    ("https://news.google.com/rss/search?q=%22additive+manufacturing%22+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen", "增材制造"),
]

# 关键词 -> 卡片标签
TAG_RULES = [
    (r"printer|scanner|launch|unveil|release|review|打印机|扫描仪|发布|新品", "hw"),
    (r"raises|funding|acqui|IPO|market|sales|deal|工厂|融资|收购|市场", "biz"),
    (r"research|study|universit|MIT|simulation|material|研究|大学|材料|仿真", "tech"),
    (r"medical|dental|implant|aerospace|space|housing|concrete|医疗|植入|航空|建筑", "app"),
]
def guess_tag(text):
    for pat, tag in TAG_RULES:
        if re.search(pat, text, re.I):
            return tag
    return "tech"

def parse_rss(url, source_name, limit=8):
    """通用 RSS/Atom 解析（用 html.parser 兼容，无需 lxml）"""
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        r.raise_for_status()
    except Exception as e:
        print(f"  [WARN] {source_name} RSS failed: {e}")
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    entries = soup.find_all("item") or soup.find_all("entry")
    for item in entries[:limit]:
        title = item.find("title")
        link_el = item.find("link")
        desc = item.find("description") or item.find("summary")
        pub = item.find("pubdate") or item.find("published") or item.find("updated")
        if not title or not link_el:
            continue
        title_text = title.get_text(strip=True)
        # html.parser 会把 <link> 当空标签，URL 文本在 next_sibling
        link_text = link_el.get_text(strip=True) or link_el.get("href", "")
        if not link_text and link_el.next_sibling:
            link_text = str(link_el.next_sibling).strip()
        # Google News 标题格式: "标题 - 来源名"
        who = ""
        if " - " in title_text:
            head, tail = title_text.rsplit(" - ", 1)
            if head and len(tail) <= 40:
                title_text, who = head.strip(), tail.strip()
        # Google News 的 description 是标题+来源的拼接垃圾，直接丢弃
        desc_text = ""
        date_str = ""
        if pub:
            ptext = pub.get_text(strip=True)
            for fmt in ("%a, %d %b %Y", "%Y-%m-%d"):
                try:
                    date_str = datetime.strptime(ptext[:16], fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
            if not date_str:
                dm = re.search(r"(\d{4})-(\d{2})-(\d{2})", ptext)
                if dm:
                    date_str = dm.group(0)
        if title_text and link_text:
            items.append({
                "title": title_text,
                "url": link_text,
                "desc": desc_text,
                "date": date_str or datetime.now().strftime("%Y-%m-%d"),
                "source": source_name,
                "who": who,
                "tag": guess_tag(title_text + " " + desc_text)
            })
    return items

def update_news():
    print("[news] fetching RSS feeds...")
    all_items = []
    for url, name in FEEDS:
        all_items.extend(parse_rss(url, name))
        time.sleep(1)

    # 去重（按 URL）
    seen = set()
    uniq = []
    for it in all_items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        uniq.append(it)

    # 按日期倒序，取前 12
    def sort_key(x):
        try:
            return datetime.strptime(x["date"], "%Y-%m-%d")
        except ValueError:
            return datetime.min
    uniq.sort(key=sort_key, reverse=True)
    uniq = uniq[:12]

    payload = {
        "generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "source": ["Google News RSS"],
        "items": uniq
    }
    save("news", payload)

# ============================================================
# 2. 抓取 GitHub Trending（模拟浏览器解析页面）
# ============================================================
LANG_COLORS = {
    "python": "#3572A5",
    "typescript": "#3178c6",
    "javascript": "#f1e05a",
    "rust": "#dea584",
    "go": "#00ADD8",
    "c++": "#f34b7d",
    "c": "#555555",
    "java": "#b07219",
    "vue": "#41b883",
    "html": "#e34c26",
    "css": "#563d7c",
    "swift": "#ffac45",
    "kotlin": "#A97BFF",
}

def fetch_github_trending():
    """抓取 GitHub Trending 页面（今日）"""
    url = "https://github.com/trending"
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        r.raise_for_status()
    except Exception as e:
        print(f"  [WARN] GitHub Trending fetch failed: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    # GitHub Trending 页面结构（2024-2026）
    for article in soup.select("article.Box-row")[:10]:
        h2 = article.select_one("h2 a")
        if not h2:
            continue
        name = h2.get_text(strip=True).replace("\n", "").replace(" ", "")
        href = h2.get("href", "")
        if not href.startswith("/"):
            href = "/" + href
        repo_url = f"https://github.com{href}"

        # 描述
        desc_el = article.select_one("p.col-9")
        desc = desc_el.get_text(strip=True) if desc_el else ""

        # 语言
        lang_el = article.select_one("span[itemprop='programmingLanguage']")
        lang = lang_el.get_text(strip=True) if lang_el else ""

        # Star 数（总 star）
        stars_el = article.select_one("a[href$='/stargazers']")
        stars = ""
        if stars_el:
            raw = stars_el.get_text(strip=True).replace(",", "")
            if raw.isdigit():
                n = int(raw)
                stars = f"{n/1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)

        color = LANG_COLORS.get(lang.lower(), "#8b98ad")
        items.append({
            "name": name,
            "url": repo_url,
            "desc": desc or "热门开源项目",
            "stars": stars or "新晋",
            "lang": lang or "多语言",
            "color": color
        })

    return items[:8]

def update_trending():
    print("[trending] fetching github.com/trending ...")
    items = fetch_github_trending()
    payload = {
        "generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "source": "github.com/trending",
        "items": items
    }
    save("trending", payload)

# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="3D打印前线数据自动更新")
    parser.add_argument("--news", action="store_true", help="仅更新新闻")
    parser.add_argument("--trending", action="store_true", help="仅更新 GitHub 趋势")
    args = parser.parse_args()

    do_news = not args.trending
    do_trending = not args.news

    if do_news:
        update_news()
    if do_trending:
        update_trending()

    print("[done]")

if __name__ == "__main__":
    main()
