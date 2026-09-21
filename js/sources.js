// 来源登记 · 优先 window.RPT.sources（自动引擎），否则退回静态表
(function renderSources() {
  const host = document.getElementById("source-list");
  if (!host) return;
  const catName = {
    company: "公司/监管一手",
    broker: "媒体转引",
    industry: "官方/行业",
    kimi: "数据商汇编",
    local: "本地行情",
    news: "公开快讯",
  };
  const auto = (window.RPT && window.RPT.sources) || [];
  const list = auto.length ? auto : (window.SOURCES || []);
  const day = (window.RPT && window.RPT.meta && window.RPT.meta.reviewDay) || "";
  const title = document.querySelector("#sec-sources .chart-title");
  if (title) {
    title.textContent = auto.length
      ? `来源登记 · 自动引擎 ${day}`
      : "来源登记 · 静态样例";
  }
  const sub = document.querySelector("#sec-sources .chart-sub");
  if (sub) {
    sub.textContent = auto.length
      ? `本页关键数字出处 · 复盘日 ${day} · 行情=qlib/Parquet/DuckDB+东财 · 新闻=新浪/东财`
      : "本页每个关键数字的出处与日期";
  }
  if (!list.length) {
    host.innerHTML = `<p class="muted">暂无来源登记</p>`;
    return;
  }
  host.innerHTML = list.map(s =>
    `<div class="src-row"><span class="s-fact"><b>${s.id}</b>　${s.fact}</span>
     <span class="s-cite"><span class="src-cat ${s.cat || "kimi"}">${catName[s.cat] || s.cat || "来源"}</span>${s.cite}</span></div>`
  ).join("");
})();
