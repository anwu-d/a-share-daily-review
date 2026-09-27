// 主控 · chips 导航 / 入场动画 / data-drill / sticky 章节导航
(() => {
  // chips 平滑滚动
  document.querySelectorAll("[data-goto]").forEach(b =>
    b.addEventListener("click", () => {
      const t = document.querySelector(b.dataset.goto);
      if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
    }));

  // 入场动画
  const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const els = document.querySelectorAll(".band .prose > *, .band .wide, .band .wide.xl, footer .prose > *, footer .wide.xl");
  if (REDUCE) els.forEach(el => el.classList.add("in"));
  else {
    els.forEach(el => el.classList.add("rv"));
    const io = new IntersectionObserver(es => es.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { threshold: 0.1 });
    els.forEach(el => io.observe(el));
    setTimeout(() => els.forEach(el => {
      if (!el.classList.contains("in") && el.getBoundingClientRect().top < innerHeight * 0.96) el.classList.add("in");
    }), 1600);
    let ticking = false;
    addEventListener("scroll", () => {
      if (ticking) return; ticking = true;
      requestAnimationFrame(() => {
        els.forEach(el => {
          if (!el.classList.contains("in") && el.getBoundingClientRect().top < innerHeight * 0.92) el.classList.add("in");
        });
        ticking = false;
      });
    }, { passive: true });
  }

  // data-drill 通用绑定
  document.querySelectorAll("[data-drill]").forEach(el =>
    el.addEventListener("click", e => {
      const [title, value, sub, source] = el.dataset.drill.split("|");
      U.showDrill({ title, value, sub, source, x: e.clientX, y: e.clientY, trigger: el });
    }));

  // sticky 章节导航：滚过封面后显示 + 高亮当前章节
  const nav = document.getElementById("sec-nav");
  const cover = document.getElementById("cover");
  const links = nav ? [...nav.querySelectorAll("a[href^='#']")] : [];
  const sections = links
    .map(a => document.querySelector(a.getAttribute("href")))
    .filter(Boolean);

  function updateNav() {
    if (!nav || !cover) return;
    const coverBottom = cover.getBoundingClientRect().bottom;
    nav.classList.toggle("on", coverBottom <= 8);

    let active = null;
    const probe = 96;
    for (const sec of sections) {
      const r = sec.getBoundingClientRect();
      if (r.top <= probe && r.bottom > probe) { active = sec; break; }
    }
    links.forEach(a => {
      const id = a.getAttribute("href");
      a.classList.toggle("active", active && id === "#" + active.id);
    });
  }

  let navTick = false;
  addEventListener("scroll", () => {
    if (navTick) return; navTick = true;
    requestAnimationFrame(() => { updateNav(); navTick = false; });
  }, { passive: true });
  addEventListener("resize", updateNav, { passive: true });
  updateNav();
})();
