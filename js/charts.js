// 签名图 · ①情绪五维分解（canvas）②明日分支概率板（DOM · P7 odds board）
(() => {
  const D = window.RPT, P = U.PAL;
  const MONO = "Menlo, Consolas, monospace";
  const SERIF = '"Iowan Old Style", Palatino, Georgia, serif';
  const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── ① 五维分解 ── */
  (() => {
    const host = document.getElementById("dims-chart");
    if (!host) return;
    const day = (D.meta || {}).reviewDay || "";
    const dims = D.dims || [];
    const total = dims.reduce((a, d) => a + d.s, 0);
    const full = dims.reduce((a, d) => a + d.w, 0);
    const ranked = [...dims].sort((a, b) => (b.s / b.w) - (a.s / a.w));
    const best = ranked[0], worst = ranked[ranked.length - 1];
    const dTitle = best && worst
      ? `五维合计 ${total} 分 / 满分 ${full}：最稳 ${best.name} ${best.s}/${best.w}，扣分最多 ${worst.name} ${worst.s}/${worst.w}`
      : `五维合计 ${total} 分 / 满分 ${full}`;
    const body = U.frame(host, {
      title: dTitle,
      sub: `得分 / 权重满分 · 点击各行看依据 · ${day} 五维 ${total} → 明日基准 ${D.score.base36 ?? "—"}`,
      src: `engine/metrics.py 规则自算 · ${day}`,
    });
    const cv = document.createElement("canvas");
    cv.style.width = "100%"; cv.style.height = "300px"; cv.style.display = "block"; cv.style.cursor = "pointer";
    // 设计审计 A1/A3：canvas 是纯绘制，读屏与键盘用户够不到 —— 补 role/tabindex/aria
    // 并配一张视觉隐藏的文字表作为图表的等价物。
    cv.setAttribute("role", "img");
    cv.setAttribute("tabindex", "0");
    const dimsTitle = (D.dims || []).map(d => `${d.name} ${d.s}/${d.w}`).join("，");
    cv.setAttribute("aria-label", `情绪五维分解：${dimsTitle}`);
    body.appendChild(cv);
    const altWrap = document.createElement("div");
    altWrap.className = "visually-hidden";
    altWrap.innerHTML = `<table><caption>情绪五维分解</caption><thead><tr><th>维度</th><th>得分</th><th>满分</th></tr></thead><tbody>${
      (D.dims || []).map(d => `<tr><td>${d.name}</td><td>${d.s}</td><td>${d.w}</td></tr>`).join("")
    }</tbody></table>`;
    body.appendChild(altWrap);
    const bd = U.bindCanvas(cv), ctx = bd.ctx;
    let W = 0, rows = [], played = REDUCE;

    function draw(prog) {
      ctx.clearRect(0, 0, W, 300);
      const labW = 128, panelW = 168;
      const x0 = labW, x1 = W - panelW - 74, trackW = Math.max(80, x1 - x0);
      const rh = 46, y0 = 26;
      rows = [];
      D.dims.forEach((d, i) => {
        const y = y0 + i * rh;
        const lp = REDUCE ? 1 : U.clamp((prog - i * 0.12) * 2.2, 0, 1);
        ctx.font = `10.5px ${MONO}`; ctx.fillStyle = P.inkMd; ctx.textAlign = "left";
        ctx.fillText(d.name, 0, y + 14);
        ctx.fillStyle = P.lineLo; ctx.fillRect(x0, y + 4, trackW, 13);
        const fw = trackW * (d.s / d.w) * lp;
        ctx.fillStyle = P.red; ctx.fillRect(x0, y + 4, fw, 13);
        ctx.font = `700 11px ${MONO}`; ctx.fillStyle = P.ink;
        ctx.fillText(`${d.s}/${d.w}`, x0 + fw + 7, y + 15);
        ctx.strokeStyle = P.line; ctx.beginPath(); ctx.moveTo(x1, y + 2); ctx.lineTo(x1, y + 19); ctx.stroke();
        rows.push({ x: 0, y: y - 6, w: W - panelW, h: rh, d });
      });
      const px = W - panelW + 8;
      ctx.strokeStyle = P.lineLo; ctx.beginPath(); ctx.moveTo(px - 14, 18); ctx.lineTo(px - 14, 268); ctx.stroke();
      ctx.font = `10px ${MONO}`; ctx.fillStyle = P.inkLo; ctx.fillText("情绪总分", px, 34);
      ctx.font = `700 56px ${SERIF}`; ctx.fillStyle = P.ink; ctx.fillText(String(D.score.today), px, 92);
      ctx.font = `10.5px ${MONO}`; ctx.fillStyle = P.inkLo;
      const yst = D.score.yesterday;
      ctx.fillText(
        yst == null ? "昨日 无缓存" : `昨日 ${yst} · ${D.score.today - yst >= 0 ? "+" : ""}${D.score.today - yst}`,
        px, 116,
      );
      ctx.fillStyle = P.red; ctx.font = `700 12px ${MONO}`;
      ctx.fillText(`明日基准 ${D.score.base36} →`, px, 142);
      ctx.strokeStyle = P.lineLo; ctx.beginPath(); ctx.moveTo(px, 158); ctx.lineTo(px + panelW - 30, 158); ctx.stroke();
      ctx.fillStyle = P.inkLo; ctx.font = `10px ${MONO}`;
      ctx.fillText(`节点 = ${(D.cover && D.cover.title) || "—"}`, px, 178);
      ctx.fillText(`状态 = ${D.regime || "—"}`, px, 196);
      ctx.fillText(`仓位锚 = ≤${D.posAnchor ?? "—"} 成`, px, 214);
    }

    function play() {
      if (played) { draw(1); return; }
      played = true;
      let t0 = null;
      const step = ts => { if (t0 == null) t0 = ts; const p = U.clamp((ts - t0) / 1300, 0, 1); draw(p); if (p < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    }
    function fitDraw() { const r = bd.fit(); W = r.w; draw(played ? 1 : 0); }
    new IntersectionObserver((es, io) => es.forEach(e => { if (e.isIntersecting) { play(); io.disconnect(); } }), { threshold: 0.2 }).observe(cv);
    cv.addEventListener("click", e => {
      const r = cv.getBoundingClientRect(), y = e.clientY - r.top;
      const row = rows.find(rr => y >= rr.y && y <= rr.y + rr.h);
      if (row) U.showDrill({ title: `情绪五维 · ${row.d.name}`, value: `${row.d.s} / ${row.d.w}`, sub: row.d.note, source: `engine/metrics.py 规则自算 · ${day}`, x: e.clientX, y: e.clientY, trigger: cv });
    });
    let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(fitDraw, 180); });
    fitDraw();
  })();

  /* ── ② 明日分支概率板 ── */
  (() => {
    const host = document.getElementById("odds-chart");
    if (!host) return;
    const day = (D.meta || {}).reviewDay || "";
    const branches = D.branches || [];
    const base = branches.find(b => b.base) || branches[0] || {};
    const body = U.frame(host, {
      title: base.key ? `明日三分支：基准 ${base.score} 分（${base.dir}）` : "明日三分支",
      sub: "概率刻度 0–60%（数值为编辑性 [推算]，非统计频率）· 点击圆点看触发条件 · 蓝色行 = 基准情形",
      src: `engine/metrics.py 三支预判 · ${day}`,
    });
    const wrap = document.createElement("div"); wrap.className = "odds";
    branches.forEach(b => {
      const row = document.createElement("div");
      row.className = "odd-row" + (b.base ? " base" : "");
      row.innerHTML = `
        <div class="o-name"><b>${b.key} · ${b.score} 分</b><span>${b.dir}</span></div>
        <div class="o-scale"><div class="o-track"><i data-drill-keep style="left:${b.prob / 60 * 100}%"></i></div><em>${b.prob}%</em></div>
        <div class="o-ev">${b.cond}</div>`;
      row.querySelector("i").addEventListener("click", e => {
        U.showDrill({ title: `明日分支 · ${b.key}`, value: `${b.prob}%`, sub: `方向=${b.dir}（${b.score} 分）｜触发：${b.cond}`, source: `engine/metrics.py 三支预判 · ${day}`, x: e.clientX, y: e.clientY });
      });
      if (b.base) {
        const pill = document.createElement("span"); pill.className = "o-pill"; pill.textContent = "基准情形";
        row.querySelector(".o-name").appendChild(pill);
      }
      wrap.appendChild(row);
    });
    const ax = document.createElement("div"); ax.className = "o-axis";
    ax.innerHTML = '<span></span><div class="ax-mid"><span>0</span><span>25%</span><span>60%</span></div><span></span>';
    wrap.appendChild(ax);
    body.appendChild(wrap);
  })();
})();
