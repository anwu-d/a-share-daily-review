# -*- coding: utf-8 -*-
"""业务转型股「未被充分定价」模块。

分层（与 spec docs/compose/spec/transition-underpricing.md 对应）：
  sources_cninfo  巨潮公告检索 / 全文检索 / PDF 下载
  sources_irm     互动易 + 全景网问答
  sources_em      东财研报 / 一致预期 / 财报 / 公告分类
  esop_extract    激励考核目标抽取（模式族 + 表头驱动）
  milestone_extract 里程碑措辞定级
  score           两个分数 + 分项
  ingest          全市场采集
  scan            生成 js/data.js 的 transition 段

所有取数函数都遵循同一条纪律：**静默失效必须变成显式错误**。
调研实测巨潮/东财多处非法参数会返回全市场或 0 条而不报错，
因此每个源函数都要做量级自校验，而不是相信「HTTP 200 就是成功」。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from config import DATA_DIR, ROOT

TRANSITION_DIR = DATA_DIR / "transition"
TRANSITION_DIR.mkdir(parents=True, exist_ok=True)

COVERAGE_PATH = TRANSITION_DIR / "coverage.json"

# 巨潮全部端点（含 JSON 数据文件与 PDF 静态站）强制浏览器 UA，否则 403。
# Referer / Cookie 均不需要（实测）。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 限速：调研实测巨潮 40 次连发、东财 80 次连发均无 429，
# 但那是 10 秒窗口的爆发测试，不代表长期安全。本地日更按 ≤5 req/s 走。
MIN_INTERVAL = 0.2
_last_call = [0.0]


class SourceError(RuntimeError):
    """取数失败或触发了静默失效防护。"""


def _throttle() -> None:
    dt = time.time() - _last_call[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last_call[0] = time.time()


def http(method: str, url: str, *, data=None, params=None, timeout: float = 25.0,
         retries: int = 2, expect_json: bool = True):
    """带 UA、限速与指数退避的请求。expect_json 为真时返回解析后的 JSON。"""
    import requests

    headers = {"User-Agent": BROWSER_UA}
    if method.upper() == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    last = None
    for attempt in range(retries + 1):
        _throttle()
        try:
            r = requests.request(method.upper(), url, data=data, params=params,
                                 headers=headers, timeout=timeout)
            if r.status_code >= 500:
                raise SourceError(f"HTTP {r.status_code} from {url}")
            if r.status_code != 200:
                raise SourceError(f"HTTP {r.status_code} from {url}")
            if not expect_json:
                return r
            try:
                return r.json()
            except ValueError as e:
                raise SourceError(f"非 JSON 响应（疑似被 WAF 拦或参数错）: {url}") from e
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise SourceError(f"{method} {url} 失败: {last}")


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
