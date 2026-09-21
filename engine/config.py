# 每日复盘引擎配置
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
DATA_DIR = ROOT / "data"
JS_DATA = ROOT / "js" / "data.js"
INDEX_HTML = ROOT / "index.html"

# 把 vendor 加入 path（MyTT / Ashare）
import sys
for sub in ("MyTT", "Ashare"):
    p = str(VENDOR / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# ── 情绪五维权重（合计 100）──
DIM_WEIGHTS = {
    "涨跌停结构": 25,
    "赚钱效应延续性": 25,
    "市场广度": 20,
    "量能风偏": 20,
    "主线健康度": 10,
}

# 涨跌停判定
ZT_TOLERANCE = 0.002   # 收盘 >= 涨停价*(1-0.2%) 视为涨停
ZB_MIN_TOUCH = 0.095   # 盘中最高触及涨停价*95% 以上且未收涨停 → 炸板（粗判）

# 晋级率：分母取前一交易日连板数 >= 2 的股票
PROMOTE_MIN_LBC = 2

# 用户自选池（可改；空则只用数据驱动生成）
WATCH_POOL = [
    {"name": "中际旭创", "code": "300308", "dir": "PCB/硬件"},
    {"name": "景旺电子", "code": "603228", "dir": "PCB/硬件"},
    {"name": "香农芯创", "code": "300475", "dir": "存储"},
    {"name": "亚盛集团", "code": "600108", "dir": "农业"},
    {"name": "新希望", "code": "000876", "dir": "农业"},
    {"name": "金健米业", "code": "600127", "dir": "农业"},
    {"name": "龙版传媒", "code": "605577", "dir": "传媒/AI"},
    {"name": "易点天下", "code": "301171", "dir": "传媒/AI"},
    {"name": "港迪技术", "code": "301633", "dir": "数据中心"},
    {"name": "黄河旋风", "code": "600172", "dir": "超硬材料"},
]

# 板块关键词 → 主线归类（题材口径粗分）
SECTOR_KEYWORDS = {
    "PCB/算力硬件": ["沪电", "景旺", "深南", "生益", "兴森", "崇达", "奥士康", "世运", "金安", "威尔", "中富"],
    "光模块/CPO": ["中际", "新易盛", "天孚", "光迅", "华工", "光库", "源杰", "太辰光"],
    "存储": ["香农", "德明利", "佰维", "江波", "澜起", "兆易"],
    "农业": ["亚盛", "新希望", "敦煌", "罗牛", "中水", "金健", "北大荒", "登海"],
    "传媒/AI应用": ["龙版", "易点", "芒果", "出版", "影视", "博纳", "中广天择"],
    "大消费": ["国芳", "百大", "爱仕达", "安记", "中央商场", "人人乐"],
}
