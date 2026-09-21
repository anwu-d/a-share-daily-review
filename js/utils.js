// Shared utilities
window.U = (() => {
  const TAU = Math.PI * 2;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  function bindCanvas(canvas) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const fit = () => {
      const r = canvas.getBoundingClientRect();
      canvas.width = Math.round(r.width * dpr);
      canvas.height = Math.round(r.height * dpr);
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w: r.width, h: r.height, cx: r.width / 2, cy: r.height / 2 };
    };
    return { fit, ctx: canvas.getContext("2d") };
  }

  const PAL = {
    paper: "#ffffff", hi: "#f7f9fc", ink: "#051c2c", inkMd: "#42566a", inkLo: "#8595a6",
    line: "#dbe2ea", lineLo: "#eef1f6", red: "#2251ff", redHi: "#1233b8",
    blue: "#7a45c9", copper: "#b07a10", green: "#008a6d", gold: "#b07a10",
    neg: "#c22f4e", accent: "#2251ff", navy: "#051c2c",
  };

  const fmt = {
    b: v => v == null ? "—" : "$" + (v >= 100 ? v.toFixed(0) : v.toFixed(1)) + "B",
    pct: v => (v > 0 ? "+" : "") + v.toFixed(v % 1 ? 1 : 0) + "%",
    n: v => v.toLocaleString("en-US"),
  };

  const drill = document.getElementById("drill-card");
  let drillOpen = false;
  function showDrill({ title, value, delta, sub, source, x, y }) {
    drill.innerHTML = `<button class="d-close">✕</button>
      <div class="d-title">${title}</div>
      <div class="d-val">${value}${delta != null ? ` <span class="${delta >= 0 ? "pos" : "neg"}" style="font-size:15px">${fmt.pct(delta)}</span>` : ""}</div>
      ${sub ? `<div class="d-sub">${sub}</div>` : ""}
      ${source ? `<div class="d-src">Source · ${source}</div>` : ""}`;
    drill.hidden = false; drillOpen = true;
    const r = drill.getBoundingClientRect();
    let left = clamp(x + 14, 8, window.innerWidth - r.width - 8);
    let top = clamp(y - r.height - 14, 8, window.innerHeight - r.height - 8);
    if (y - r.height - 14 < 8) top = clamp(y + 18, 8, window.innerHeight - r.height - 8);
    drill.style.left = left + "px"; drill.style.top = top + "px";
    drill.querySelector(".d-close").onclick = hideDrill;
  }
  function hideDrill() { drill.hidden = true; drillOpen = false; }
  document.addEventListener("click", e => {
    if (drillOpen && !drill.contains(e.target)) {
      if (!e.target.closest("[data-drill-keep]")) hideDrill();
    }
  }, true);

  const tip = document.createElement("div");
  tip.className = "tip"; document.body.appendChild(tip);
  function showTip(html, x, y) {
    tip.innerHTML = html; tip.style.opacity = 1;
    tip.style.left = clamp(x + 12, 4, window.innerWidth - 220) + "px";
    tip.style.top = (y - 34) + "px";
  }
  function hideTip() { tip.style.opacity = 0; }

  function frame(el, { title, sub, src }) {
    const head = document.createElement("div");
    if (title) head.innerHTML = `<p class="chart-title">${title}</p>${sub ? `<p class="chart-sub">${sub}</p>` : ""}`;
    el.appendChild(head);
    const body = document.createElement("div");
    el.appendChild(body);
    if (src) {
      const s = document.createElement("p");
      s.className = "chart-src"; s.textContent = "Source · " + src;
      el.appendChild(s);
    }
    return body;
  }

  return { TAU, clamp, lerp, PAL, bindCanvas, fmt, showDrill, hideDrill, showTip, hideTip, frame };
})();
