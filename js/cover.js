// 封面 · 主题原子 = 涨停板的「板」：9/7 连板阶梯
// 布局：与 .cover-inner 右栏对齐，垂直居中，整幅图不侵入左栏文案。
(() => {
  const cv = document.getElementById("cover-canvas");
  if (!cv) return;
  const bd = U.bindCanvas(cv);
  const ctx = bd.ctx;
  const D = window.RPT;
  const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const MONO = "Menlo, Consolas, monospace";
  const SERIF = '"Iowan Old Style", Palatino, Georgia, serif';
  const P = U.PAL;
  let W = 0, H = 0, hits = [], plaques = [], labels = [], t0 = null, raf = 0, played = REDUCE;

  // drill 面板全部由 data.js 现场拼装，避免硬编码数字与复盘日脱节
  function drillSrc() {
    const L = D.ladder || {}, P = D.pools || {}, day = (D.meta || {}).reviewDay || "";
    const api = `东财涨停池/炸板池 API · ${day}（K1/K2/K3）`;
    return {
      first: {
        title: "首板（昨日涨停·今日首板）",
        value: `${L.first ?? "—"} 家`,
        sub: `首板 ${L.first ?? "—"} 家；涨停合计 ${P.zt ?? "—"} 家；最高板 ${L.topName || "—"} ${L.topBoards ?? "—"} 板`,
        source: api,
      },
      second: {
        title: "2 板梯队",
        value: `${L.second ?? "—"} 家`,
        sub: `2 板 ${L.second ?? "—"} 家；晋级率 ${(D.promo || {}).rate ?? "—"}%`,
        source: api,
      },
      third: {
        title: "3 板梯队",
        value: `${L.third ?? "—"} 家`,
        sub: `3 板 ${L.third ?? "—"} 家`,
        source: api,
      },
      top: {
        title: `${L.topName || "—"} · 全场最高板`,
        value: `${L.topBoards ?? "—"} 板`,
        sub: `连板高度 ${L.topBoards ?? "—"} 板；接力风险高，只作情绪温度计`,
        source: api,
      },
      crack: {
        title: "炸板（触板未封）",
        value: `${P.zb ?? "—"} 家 · 炸板率 ${P.zbRate ?? "—"}%`,
        sub: `炸板 ${P.zb ?? "—"} 家 / 涨停 ${P.zt ?? "—"} 家；炸板率 ${P.zbRate ?? "—"}%`,
        source: api,
      },
      down: {
        title: "跌停",
        value: `${P.dt ?? "—"} 家`,
        sub: `跌停 ${P.dt ?? "—"} 家；红盘率 ${(D.breadth || {}).red_pct ?? P.redPct ?? "—"}%`,
        source: api,
      },
    };
  }

  function build() {
    plaques = []; labels = []; hits = [];
    const isMobile = W < 980;

    // 与内容栏对齐：读 .cover-inner 的实际位置。
    // rect 是「视口坐标」，画布坐标 = 视口坐标 - 画布 rect 原点，
    // 否则页面滚动后重绘（resize / 缩放）会把整幅图推到画布外。
    const inner = document.querySelector(".cover-inner");
    const cr = cv.getBoundingClientRect();
    const ir = inner ? inner.getBoundingClientRect() : { left: 0, width: W, top: 0, height: H };
    const irLeft = ir.left - cr.left, irTop = ir.top - cr.top;

    // ── 舞台区 ──
    // 桌面：cover-inner 右栏，垂直居中
    // 手机：画布区（底部 38vh）全宽，水平居中
    let stage;
    if (isMobile) {
      // 画布本身已经只是底部 38vh，用画布自身坐标
      stage = { x: 8, y: 8, w: W - 16, h: H - 16 };
    } else {
      const left = irLeft + ir.width * 0.47;
      stage = {
        x: left,
        y: irTop + ir.height * 0.18,
        w: ir.width * 0.51,
        h: ir.height * 0.68,
      };
    }

    // 缩放：让内容占舞台宽度约 88%，高度不超过舞台 85%
    // 内容横向砖块数估算（cell 单位）
    const zbCols = isMobile ? 5 : 5;
    const firstCols = isMobile ? 8 : 11;
    // 炸板宽 + 间隔 + 四级台阶
    const cellsWide =
      zbCols + 1.6 +            // 炸板簇 + 间隙
      firstCols + 1.2 +         // 首板 + 间隙
      3 + 1.2 +                 // 2板
      2 + 1.2 +                 // 3板
      1.4;                      // 6板
    const scW = (stage.w * 0.88) / (cellsWide * 16.2);
    // 高度：首板约 8 行 + 阶梯抬升 + 标签
    const firstRows = Math.ceil(D.ladder.first / firstCols);
    const estRows = firstRows + 4;
    const scH = (stage.h * 0.82) / (estRows * 15.5 + 40);
    let sc = U.clamp(Math.min(scW, scH), isMobile ? 0.34 : 0.55, isMobile ? 0.55 : 1.35);

    const pw = 13 * sc, ph = 9 * sc, gap = 3.2 * sc;
    const cell = pw + gap;

    const zbRows = Math.ceil(D.pools.zb / zbCols);
    const cw = zbCols * cell + 16 * sc;
    const stepGap = 18 * sc;
    const stepDefs = [
      { cols: firstCols, n: D.ladder.first },
      { cols: 3, n: D.ladder.second },
      { cols: 2, n: D.ladder.third },
      { cols: 1, n: D.ladder.top },
    ];
    const stepWs = stepDefs.map(s => s.cols * cell + 16 * sc);
    const totalContentW =
      cw + 22 * sc + stepWs[0] + stepGap + stepWs[1] + stepGap + stepWs[2] + stepGap + stepWs[3];

    // 水平居中于舞台
    const ox = stage.x + Math.max(0, (stage.w - totalContentW) / 2);
    // 垂直：地面线落在舞台中下部，给阶梯留抬升空间
    const gy = stage.y + stage.h * 0.78;

    // ── 炸板簇 ──
    const cx0 = ox;
    const cy0 = gy - zbRows * cell - 6 * sc;
    for (let i = 0; i < D.pools.zb; i++) {
      plaques.push({
        x: cx0 + 7 * sc + (i % zbCols) * cell,
        y: cy0 + Math.floor(i / zbCols) * cell,
        w: pw, h: ph, state: "hollow", i: plaques.length,
      });
    }
    const labX = x => Math.min(x, stage.x + stage.w - 68);
    labels.push({ kind: "txt", x: cx0 + 1, y: gy + 15, s: `炸板 ${D.pools.zb}`, key: "crack" });
    hits.push({ x: cx0 - 4, y: cy0 - 8, w: cw + 6, h: gy - cy0 + 22, key: "crack" });

    // ── 跌停 ──
    for (let i = 0; i < D.pools.dt; i++) {
      plaques.push({ x: cx0 + 7 * sc + i * cell, y: gy + 18, w: pw, h: ph, state: "red", i: plaques.length });
    }
    labels.push({ kind: "txt", x: cx0 + 1, y: gy + 44, s: `跌停 ${D.pools.dt}`, key: "down", neg: true });
    hits.push({ x: cx0 - 4, y: gy + 14, w: D.pools.dt * cell + 16, h: 40, key: "down" });

    // ── 连板阶梯（台阶高度按行数，最大不超过舞台顶）──
    const rowH = cell;
    const maxLift = stage.h * 0.72; // 地面线到舞台顶可用高度
    const h1 = Math.min(firstRows * rowH + 6 * sc, maxLift * 0.42);
    const h2 = Math.min(3 * rowH + 14 * sc, maxLift * 0.28);
    const h3 = Math.min(2 * rowH + 22 * sc, maxLift * 0.36);
    const h4 = Math.min(1 * rowH + 40 * sc, maxLift * 0.48);

    const LAST = 3;
    const steps = [
      { lv: 1, n: D.ladder.first, cols: firstCols, h: h1 },
      { lv: 2, n: D.ladder.second, cols: 3, h: h2 },
      { lv: 3, n: D.ladder.third, cols: 2, h: h3 },
      { lv: 6, n: D.ladder.top, cols: 1, h: h4 },
    ];

    let sx = ox + cw + 22 * sc;
    steps.forEach((st, si) => {
      const wStep = stepWs[si];
      if (sx + wStep > stage.x + stage.w + 6) return;
      const top = gy - st.h;
      labels.push({ kind: "step", x: sx, y: gy, w: wStep, top });
      for (let i = 0; i < st.n; i++) {
        const cx = sx + 8 * sc + (i % st.cols) * cell;
        const cy = top - 3 * sc - (Math.floor(i / st.cols) + 1) * cell + gap;
        if (cy < stage.y - 6) continue;
        plaques.push({ x: cx, y: cy, w: pw, h: ph, state: si === LAST ? "top" : "solid", i: plaques.length });
      }
      const key = si === 0 ? "first" : si === 1 ? "second" : si === 2 ? "third" : "top";
      const lb = si === LAST ? `${D.ladder.topName} · ${D.ladder.topBoards} 板` : `${st.lv} 板 × ${st.n}`;
      if (isMobile && si === LAST) {
        labels.push({ kind: "txt", x: Math.min(sx + wStep, W - 6), y: Math.max(stage.y + 12, top - 12), s: lb, key, right: true });
      } else if (si === LAST) {
        labels.push({ kind: "txt", x: labX(sx + 1), y: Math.max(stage.y + 12, top - 12), s: lb, key });
      } else {
        labels.push({ kind: "txt", x: labX(sx + 1), y: gy + 15, s: lb, key });
      }
      const hitTop = si === LAST ? Math.max(stage.y, top - 28 * sc) : Math.max(stage.y, top - 22 * sc);
      hits.push({ x: sx - 4, y: hitTop, w: wStep + 8, h: gy - hitTop + 18, key });
      sx += wStep + stepGap;
    });

    labels.push({
      kind: "ground",
      y: gy,
      x0: ox - 4 * sc,
      x1: Math.min(stage.x + stage.w - 2, sx - stepGap),
    });
  }

  function drawPlaque(p, t) {
    const a = U.clamp(t, 0, 1);
    if (a <= 0) return;
    const sc = 0.55 + 0.45 * a;
    const w = p.w * sc, h = p.h * sc;
    const x = p.x + (p.w - w) / 2, y = p.y + (p.h - h) / 2;
    ctx.save(); ctx.globalAlpha = a;
    if (p.state === "solid") {
      ctx.fillStyle = P.ink; ctx.fillRect(x, y, w, h);
    } else if (p.state === "top") {
      ctx.fillStyle = P.red; ctx.fillRect(x, y, w * 2.1, h * 1.9);
      ctx.strokeStyle = P.neg; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.2;
      ctx.strokeRect(x - 3, y - 3, w * 2.1 + 6, h * 1.9 + 6); ctx.setLineDash([]);
    } else if (p.state === "hollow") {
      ctx.strokeStyle = P.inkLo; ctx.setLineDash([2.5, 2.5]); ctx.lineWidth = 1;
      ctx.strokeRect(x, y, w, h); ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(x + w * 0.25, y); ctx.lineTo(x + w * 0.6, y + h); ctx.stroke();
    } else if (p.state === "red") {
      ctx.fillStyle = P.neg; ctx.fillRect(x, y, w, h);
    }
    ctx.restore();
  }

  function draw(prog) {
    ctx.clearRect(0, 0, W, H);
    const gp = REDUCE ? 1 : U.clamp(prog * 3, 0, 1);
    ctx.save(); ctx.globalAlpha = gp;
    labels.forEach(l => {
      if (l.kind === "ground") {
        ctx.strokeStyle = P.ink; ctx.lineWidth = 1.3;
        ctx.beginPath(); ctx.moveTo(l.x0, l.y); ctx.lineTo(l.x1, l.y); ctx.stroke();
      } else if (l.kind === "step") {
        ctx.strokeStyle = P.inkMd; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(l.x, l.y); ctx.lineTo(l.x, l.top); ctx.lineTo(l.x + l.w, l.top); ctx.stroke();
        ctx.strokeStyle = P.line;
        ctx.beginPath(); ctx.moveTo(l.x, l.y); ctx.lineTo(l.x + l.w, l.y); ctx.stroke();
      } else if (l.kind === "txt") {
        ctx.font = `10px ${MONO}`; ctx.fillStyle = l.neg ? P.neg : P.inkLo;
        ctx.strokeStyle = "rgba(255,255,255,.92)"; ctx.lineWidth = 3.5; ctx.lineJoin = "round";
        if (l.right) ctx.textAlign = "right";
        ctx.strokeText(l.s, l.x, l.y); ctx.fillText(l.s, l.x, l.y);
        ctx.textAlign = "left";
      }
    });
    ctx.restore();
    plaques.forEach(p => {
      const local = REDUCE ? 1 : U.clamp((prog * 1.9 - p.i * 0.005 - 0.08) * 3.2, 0, 1);
      drawPlaque(p, local);
    });
    const topHit = hits.find(h => h.key === "top");
    if (topHit && gp > 0.9) {
      ctx.font = `700 11px ${SERIF}`; ctx.fillStyle = P.neg;
      ctx.strokeStyle = "rgba(255,255,255,.92)"; ctx.lineWidth = 4; ctx.lineJoin = "round";
      const tx = Math.min(topHit.x + 2, W - 78), ty = topHit.y + 12;
      ctx.strokeText("⚠ 监管警示", tx, ty); ctx.fillText("⚠ 监管警示", tx, ty);
    }
  }

  function frame(ts) {
    if (t0 == null) t0 = ts;
    const prog = U.clamp((ts - t0) / 1900, 0, 1);
    draw(prog);
    if (prog < 1) raf = requestAnimationFrame(frame);
  }

  function start() {
    const r = bd.fit(); W = r.w; H = r.h; build();
    cancelAnimationFrame(raf); t0 = null;
    // 入场动画只播一次：resize / 打印重排后直接画终态，
    // 否则任何重绘都会把整幅图退回到淡入过程中的某一帧。
    if (played) { draw(1); return; }
    played = true;
    raf = requestAnimationFrame(frame);
  }

  cv.addEventListener("click", e => {
    const r = cv.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const h = hits.find(h => x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h);
    if (h) { const s = drillSrc()[h.key]; if (s) U.showDrill({ ...s, x: e.clientX, y: e.clientY }); }
  });

  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(start, 180); });
  start();
})();
