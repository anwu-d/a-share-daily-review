# -*- coding: utf-8 -*-
"""
新闻 / 政策 / 外围 快讯源（免费公开接口）
- 新浪财经 7×24（稳定）
- 东方财富快讯（稳定）
- 财联社电报（需签名，失败则跳过）
返回统一结构: [{time, title, summary, source, tag, tags, url}]
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

import requests

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}
_s = requests.Session()
_s.headers.update(UA)


def _clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ts_to_str(ts: Any) -> str:
    try:
        if isinstance(ts, (int, float)):
            # sina 有时是秒
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        return str(ts)
    except Exception:
        return str(ts)


# ── 新浪 7×24 ───────────────────────────────────────────
def sina_7x24(limit: int = 40) -> list[dict]:
    url = "https://zhibo.sina.com.cn/api/zhibo/feed"
    params = {
        "page": 1,
        "page_size": limit,
        "zhibo_id": 152,
        "tag_id": 0,
        "dire": "f",
        "dpc": 1,
        "type": 0,
    }
    try:
        r = _s.get(url, params=params, timeout=12)
        data = r.json()
        # 结构: result.data.feed.list
        root = data.get("result") or {}
        feed = (root.get("data") or {}).get("feed") or {}
        rows = feed.get("list") or []
        out = []
        for it in rows:
            content = _clean_html(it.get("rich_text") or "")
            if not content:
                continue
            tags = it.get("tag") or []
            tag_name = ""
            if tags and isinstance(tags, list) and isinstance(tags[0], dict):
                tag_name = tags[0].get("name") or ""
            out.append(
                {
                    "time": _ts_to_str(it.get("create_time") or it.get("update_time")),
                    "title": content[:40] + ("…" if len(content) > 40 else ""),
                    "summary": content[:220],
                    "source": "新浪7×24",
                    "tag": tag_name or "flash",
                    "url": "",
                }
            )
        return out
    except Exception as e:
        print(f"[news] 新浪7×24失败: {e}")
        return []


# ── 东方财富快讯 ─────────────────────────────────────────
def eastmoney_flash(limit: int = 30) -> list[dict]:
    url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
    params = {
        "client": "web",
        "biz": "web_news",
        "column": "350,35,469",
        "order": 1,
        "needInteractData": 0,
        "page_index": 1,
        "page_size": limit,
        "req_trace": str(int(time.time() * 1000)),
    }
    headers = {**UA, "Referer": "https://kuaixun.eastmoney.com/"}
    try:
        r = _s.get(url, params=params, headers=headers, timeout=12)
        data = r.json()
        if str(data.get("code")) not in ("1", "0", "success"):
            # 仍尝试读 list
            pass
        rows = ((data.get("data") or {}).get("list")) or []
        out = []
        for it in rows:
            title = _clean_html(it.get("title") or "")
            digest = _clean_html(it.get("digest") or it.get("summary") or it.get("content") or "")
            if not title and digest:
                title = digest[:40]
            if not title:
                continue
            out.append(
                {
                    "time": (it.get("showtime") or it.get("display_time") or it.get("notice_date") or it.get("pubtime") or it.get("createTime") or it.get("showTime") or ""),
                    "title": title[:60],
                    "summary": digest[:200],
                    "source": "东财快讯",
                    "tag": "flash",
                    "url": it.get("url_w") or it.get("url_m") or it.get("url") or "",
                }
            )
        return out
    except Exception as e:
        print(f"[news] 东财快讯失败: {e}")
        return []


# ── 财联社电报（尽力而为）──────────────────────────────────
def cls_telegraph(limit: int = 30) -> list[dict]:
    """旧接口 404 / 新接口需签名；失败返回空，不阻断流水线。"""
    urls = [
        "https://www.cls.cn/nodeapi/updateTelegraphList",
        "https://www.cls.cn/api/sw?app=CailianpressWeb&os=web&sv=7.7.5",
    ]
    headers = {**UA, "Referer": "https://www.cls.cn/telegraph"}
    for url in urls:
        try:
            params = {"app": "CailianpressWeb", "os": "web", "sv": "7.7.5", "rn": limit}
            if "sw?" in url:
                params = {"type": "telegram", "rn": limit}
            r = _s.get(url, params=params, headers=headers, timeout=10)
            if r.status_code != 200 or not r.text.strip().startswith("{"):
                continue
            data = r.json()
            rows = (data.get("data") or {}).get("roll_data") or (data.get("data") or {}).get("list") or []
            if not rows:
                continue
            out = []
            for it in rows:
                title = _clean_html(it.get("title") or it.get("brief") or "")
                content = _clean_html(it.get("content") or it.get("brief") or "")
                if not title:
                    title = content[:40]
                if not title:
                    continue
                out.append(
                    {
                        "time": _ts_to_str(it.get("ctime") or it.get("modified_time")),
                        "title": title[:60],
                        "summary": content[:200],
                        "source": "财联社",
                        "tag": "telegraph",
                        "url": f"https://www.cls.cn/detail/{it.get('id','')}" if it.get("id") else "",
                    }
                )
            if out:
                return out
        except Exception:
            continue
    return []


# ── 关键词过滤 / 分类 ─────────────────────────────────────
POLICY_KW = ["央行", "证监会", "国务院", "发改委", "财政部", "金融监管", "降准", "降息", "IPO", "注册制", "监管", "政策", "MLF", "LPR", "逆回购"]
GLOBAL_KW = ["美股", "纳斯达克", "标普", "道琼斯", "美联储", "CPI", "非农", "港股", "日经", "韩股", "SK海力士", "英伟达", "费半", "欧洲央行", "美元", "原油", "黄金"]
EVENT_KW = ["发布会", "苹果", "华为", "大会", "CPI", "非农", "加息", "降息", "峰会", "数据"]
RISK_KW = ["风险", "警示", "监管函", "立案", "退市", "爆仓", "冲突", "战争", "制裁", "油价", "地缘"]


def classify_news(item: dict) -> list[str]:
    text = (item.get("title") or "") + (item.get("summary") or "")
    tags = []
    if any(k in text for k in POLICY_KW):
        tags.append("政策")
    if any(k in text for k in GLOBAL_KW):
        tags.append("外围")
    if any(k in text for k in EVENT_KW):
        tags.append("事件")
    if any(k in text for k in RISK_KW):
        tags.append("风险")
    if not tags:
        tags.append(item.get("tag") or "快讯")
    return tags


def _in_window(time_str: str, date: str, hours: int = 48) -> bool:
    if not time_str:
        return True
    try:
        s = time_str.replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m-%d %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                if fmt.startswith("%m"):
                    dt = dt.replace(year=int(date[:4]))
                target = datetime.strptime(date, "%Y-%m-%d")
                return abs((dt - target).total_seconds()) <= hours * 3600
            except ValueError:
                continue
        return date in time_str
    except Exception:
        return True


def fetch_all_news(date: str | None = None, per_source: int = 30) -> dict:
    items: list[dict] = []
    ok = []

    print("[news] 新浪 7×24 ...")
    n = sina_7x24(per_source)
    if n:
        items.extend(n)
        ok.append("sina")
    time.sleep(0.15)

    print("[news] 东财快讯 ...")
    e = eastmoney_flash(per_source)
    if e:
        items.extend(e)
        ok.append("eastmoney")
    time.sleep(0.15)

    print("[news] 财联社电报 ...")
    c = cls_telegraph(max(10, per_source // 2))
    if c:
        items.extend(c)
        ok.append("cls")

    if date:
        filtered = [x for x in items if _in_window(x.get("time", ""), date)]
        items = filtered or items

    for it in items:
        it["tags"] = classify_news(it)

    seen: set = set()
    uniq = []
    for it in items:
        key = (it.get("title") or "")[:24]
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    def pick(tag: str, n: int = 8) -> list[dict]:
        return [x for x in uniq if tag in x.get("tags", [])][:n]

    return {
        "all": uniq[:80],
        "policy": pick("政策", 10),
        "global": pick("外围", 10),
        "event": pick("事件", 8),
        "risk": pick("风险", 8),
        "sources_ok": ok,
        "count": len(uniq),
    }




# ── 设计审计修复：摘要回退 / 同主题去重 / 日期过滤 ─────────────────────────
# 审计发现（output/design-audit/AUDIT.md R5）：
#   1) 源数据无独立摘要时 summary 会等于 title，前端把标题渲染两遍
#   2) 同主题条目（如连续多条加拿大央行纪要）标题前 12 字相同却未合并
#   3) 复盘日之后的快讯（如 09-17）出现在 09-16 的复盘页上，与页头「数据截至」冲突
_CATS = ("policy", "global", "event", "risk")


def _title_key(title: str) -> str:
    """归一化标题作去重键：去标点与空白，取前 12 字。"""
    t = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]", "", title or "")
    return t[:12]


def _news_date(text: str, year: str) -> str | None:
    """从 time 字符串抽出 YYYY-MM-DD；无年份时用复盘日的年份补齐。"""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text or "")
    if m:
        return m.group(0)
    m = re.search(r"(\d{2})-(\d{2})", text or "")
    if m and year:
        return f"{year}-{m.group(1)}-{m.group(2)}"
    return None


def clean_pack(pack: dict, review_day: str, allow_late: bool = False) -> dict:
    """就地清洗 news pack，返回同一个 dict。

    - summary 与 title 相同（含仅有空白差异）时置空，前端据此不渲染摘要行
    - 同一分类内按 _title_key 去重，保留最早出现的一条
    - time 晚于 review_day 的条目默认剔除（复盘页只呈现复盘日及以前的信息）；
      allow_late=True 时保留但给 summary 打上「复盘日后」标记。
      这个开关用于**历史日重跑**：新闻源只给当天快讯，若复盘日不是今天，
      严格过滤会把整个板块清空——诚实的做法是保留并标注，而不是静默展示过期口径。
    """
    year = str(review_day or "")[:4]
    norm_title = lambda s: re.sub(r"\s+", "", s or "")
    for cat in _CATS:
        items = pack.get(cat) or []
        # 先按时间升序排序再去重，才能落实「保留最早一条」——
        # 若源列表是最新在前，直接按列表顺序取首条会把最新那条留下。
        items = sorted(items, key=lambda x: str(x.get("time") or ""))
        out, seen = [], set()
        for it in items:
            ttl = norm_title(it.get("title"))
            if not ttl:
                continue
            sm = norm_title(it.get("summary"))
            it = dict(it)
            it["summary"] = "" if (not sm or sm == ttl) else it.get("summary")
            d = _news_date(it.get("time") or "", year)
            if review_day and d and d > str(review_day):
                if not allow_late:
                    continue
                it["summary"] = ("【复盘日后】" + (it.get("summary") or "")).strip()
            key = _title_key(it.get("title"))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(it)
        pack[cat] = out
    pack["count"] = sum(len(pack.get(c) or []) for c in _CATS)
    return pack


if __name__ == "__main__":
    print(json.dumps(fetch_all_news(), ensure_ascii=False, indent=2)[:2500])
