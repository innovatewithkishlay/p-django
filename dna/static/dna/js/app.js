/* Codebase DNA — small progressive enhancements. The pages work without this file. */
(function () {
  "use strict";

  var root = document.documentElement;

  // ---- theme ------------------------------------------------------------
  function currentTheme() {
    var explicit = root.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("dna-theme", next); } catch (e) {}
      drawCharts();
    });
  });

  // ---- small behaviours ---------------------------------------------------
  document.querySelectorAll("[data-fill]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById("id_repo");
      if (!input) return;
      input.value = btn.getAttribute("data-fill");
      input.focus();
    });
  });
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });
  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.addEventListener("click", function (e) {
      if (e.target.closest("a")) return;
      window.location = row.getAttribute("data-href");
    });
  });
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.menu[open]").forEach(function (menu) {
      if (!menu.contains(e.target)) menu.removeAttribute("open");
    });
  });

  // ---- progress polling while an analysis runs --------------------------
  var poll = document.querySelector("[data-poll]");
  if (poll) {
    var url = poll.getAttribute("data-poll");
    var stage = poll.querySelector("[data-stage]");
    var bar = poll.querySelector("[data-bar]");
    var tick = function () {
      fetch(url, { headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (stage) stage.textContent = data.stage;
          if (bar) bar.style.width = Math.max(4, data.progress) + "%";
          if (data.status === "done" || data.status === "failed") {
            window.location.reload();
          } else {
            setTimeout(tick, 1500);
          }
        })
        .catch(function () { setTimeout(tick, 4000); });
    };
    setTimeout(tick, 1200);
  }

  // ---- charts -------------------------------------------------------------
  function readJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }
  function css(name) { return getComputedStyle(root).getPropertyValue(name).trim(); }

  var charts = [];
  function make(id, config) {
    var canvas = document.getElementById(id);
    if (!canvas || !window.Chart) return;
    // Chart.js sizes itself to its parent; give it a parent with a fixed height
    // so the chart cannot keep growing the card it sits in.
    if (!canvas.parentElement.classList.contains("chart-box")) {
      var box = document.createElement("div");
      box.className = "chart-box";
      box.style.height = (parseInt(canvas.getAttribute("height"), 10) || 240) + "px";
      canvas.parentElement.insertBefore(box, canvas);
      box.appendChild(canvas);
    }
    charts.push(new window.Chart(canvas, config));
  }

  function baseOptions(extra) {
    var ink = css("--ink-2"), line = css("--line"), muted = css("--muted");
    var opts = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: ink, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "rectRounded", font: { family: "Instrument Sans", size: 12 } } },
        tooltip: {
          backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: ink,
          borderColor: css("--line-2"), borderWidth: 1, padding: 10, cornerRadius: 8,
          titleFont: { family: "Instrument Sans", weight: "600" }, bodyFont: { family: "Instrument Sans" },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: muted, font: { family: "JetBrains Mono", size: 11 } }, border: { color: line } },
        y: { grid: { color: line }, ticks: { color: muted, font: { size: 11 } }, border: { display: false } },
      },
    };
    return Object.assign(opts, extra || {});
  }

  function line(label, data, color, opts) {
    return Object.assign({
      label: label, data: data, borderColor: color, backgroundColor: color,
      borderWidth: 2, tension: 0.3, pointRadius: 3, pointHoverRadius: 5, fill: false,
    }, opts || {});
  }

  function alpha(hex, a) {
    var h = hex.replace("#", "");
    if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
    var n = parseInt(h, 16);
    return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
  }

  function drawCharts() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    if (!window.Chart) return;

    var s = readJSON("series");
    var accent = css("--accent"), t1 = css("--t1"), t2 = css("--t2"), t3 = css("--t3"), t4 = css("--t4"), t5 = css("--t5");
    var ink2 = css("--ink-2"), muted = css("--muted");

    if (s) {
      var labels = s.labels;
      var health = { y: { min: 0, max: 100, grid: { color: css("--line") }, ticks: { color: muted, stepSize: 25 }, border: { display: false } } };

      make("healthChart", {
        type: "line",
        data: { labels: labels, datasets: [line("Health", s.health, accent, { fill: true, backgroundColor: alpha(accent, 0.12), borderWidth: 2.5 })] },
        options: baseOptions({ plugins: Object.assign(baseOptions().plugins, { legend: { display: false } }), scales: Object.assign(baseOptions().scales, health) }),
      });

      make("genesChart", {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            line("Health", s.health, css("--ink"), { borderWidth: 3, borderDash: [] }),
            line("Complexity", s.complexity, "#3b6ea5"),
            line("Maintainability", s.maintainability, "#8a5fbf"),
            line("Testing", s.testing, t5),
            line("Security", s.security, t1),
            line("Dependencies", s.dependencies, t2),
            line("Documentation", s.documentation, t3),
          ],
        },
        options: baseOptions({ scales: Object.assign(baseOptions().scales, health) }),
      });

      make("sizeChart", {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            line("Source lines", s.code_lines, muted, { fill: true, backgroundColor: alpha(muted, 0.15) }),
            line("Test lines", s.test_lines, t5, { fill: true, backgroundColor: alpha(t5, 0.18) }),
          ],
        },
        options: baseOptions(),
      });

      var dual = baseOptions();
      dual.scales.y2 = { position: "right", grid: { display: false }, ticks: { color: muted }, border: { display: false } };
      make("complexityChart", {
        type: "bar",
        data: {
          labels: labels,
          datasets: [
            Object.assign(line("Avg. complexity", s.avg_complexity, t2), { type: "line", yAxisID: "y" }),
            { type: "bar", label: "Functions over 10", data: s.complex_functions, backgroundColor: alpha(t2, 0.22), borderRadius: 4, yAxisID: "y2" },
          ],
        },
        options: dual,
      });

      var dual2 = baseOptions();
      dual2.scales.y2 = { position: "right", grid: { display: false }, ticks: { color: muted, callback: function (v) { return v + "%"; } }, border: { display: false } };
      make("maintChart", {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            line("Maintainability index", s.mi, "#8a5fbf"),
            line("Duplication %", s.duplication, t1, { yAxisID: "y2", borderDash: [5, 4] }),
          ],
        },
        options: dual2,
      });

      make("riskChart", {
        type: "bar",
        data: {
          labels: labels,
          datasets: [
            { label: "Risky patterns", data: s.security_issues, backgroundColor: alpha(t1, 0.75), borderRadius: 4 },
            { label: "Unpinned deps", data: s.unpinned, backgroundColor: alpha(t2, 0.6), borderRadius: 4 },
            { label: "TODO / FIXME", data: s.todo, backgroundColor: alpha(ink2, 0.25), borderRadius: 4 },
          ],
        },
        options: baseOptions(),
      });

      make("depsChart", {
        type: "line",
        data: {
          labels: labels,
          datasets: [
            line("Dependencies", s.dependency_count, ink2, { fill: true, backgroundColor: alpha(ink2, 0.08) }),
            line("Unpinned", s.unpinned, t1),
          ],
        },
        options: baseOptions(),
      });
    }

    var points = readJSON("points");
    if (points && points.length) {
      var maxLines = Math.max.apply(null, points.map(function (p) { return p.r; })) || 1;
      var colorFor = function (score) { return score >= 70 ? t1 : score >= 45 ? t2 : score >= 25 ? t3 : t4; };
      var opts = baseOptions({ interaction: { mode: "nearest", intersect: true } });
      opts.scales.x = Object.assign(opts.scales.x, { title: { display: true, text: "Changes in recent history →", color: muted }, grid: { color: css("--line") }, beginAtZero: true });
      opts.scales.y.title = { display: true, text: "Complexity →", color: muted };
      opts.scales.y.beginAtZero = true;
      opts.plugins.legend = { display: false };
      opts.plugins.tooltip.callbacks = {
        title: function (items) { return items[0].raw.path; },
        label: function (item) { var p = item.raw; return [p.x + " changes · complexity " + p.y, p.r + " lines · heat " + Math.round(p.score)]; },
      };
      opts.onClick = function () {};
      make("hotspotChart", {
        type: "bubble",
        data: {
          datasets: [{
            data: points.map(function (p) { return { x: p.x, y: p.y, r: 4 + 18 * Math.sqrt(p.r / maxLines), path: p.path, score: p.score, lines: p.r }; }),
            backgroundColor: points.map(function (p) { return alpha(colorFor(p.score), 0.55); }),
            borderColor: points.map(function (p) { return colorFor(p.score); }),
            borderWidth: 1,
          }],
        },
        options: opts,
      });
      // Show real line counts in the tooltip, not the bubble radius.
      var chart = charts[charts.length - 1];
      if (chart) {
        chart.options.plugins.tooltip.callbacks.label = function (item) {
          var p = item.raw; return [p.x + " changes · complexity " + p.y, p.lines + " lines · heat " + Math.round(p.score)];
        };
        chart.update("none");
      }
    }
  }

  if (document.readyState === "complete") drawCharts();
  else window.addEventListener("load", drawCharts);
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) mq.addEventListener("change", function () { if (!root.getAttribute("data-theme")) drawCharts(); });
  }
})();
