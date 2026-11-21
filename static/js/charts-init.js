/* ============================================================================
   COURTSIDE · Chart.js theming
   ----------------------------------------------------------------------------
   Loaded in <head> right after Chart.js so every page-level chart — including
   the ones created by inline template scripts — already sees the design-system
   palette, fonts and tooltip styling. Tokens are re-read per chart so charts
   created after a theme switch pick up the light palette.
   ========================================================================== */
(function () {
  "use strict";

  if (!window.Chart) return;

  function readTokens() {
    const styles = getComputedStyle(document.documentElement);
    const read = (name, fallback) => (styles.getPropertyValue(name) || fallback).trim();
    return {
      text: read("--text-2", "#a5a5ad"),
      muted: read("--text-3", "#74747d"),
      ink: read("--text-1", "#f5f5f7"),
      grid: read("--chart-grid", "rgba(255,255,255,.07)"),
      accent: read("--accent", "#e8863a"),
      accent2: read("--accent-2", "#f5a34f"),
      accentSoft: read("--accent-soft", "rgba(232,134,58,.14)"),
      info: read("--chart-2", "#4a9eff"),
      win: read("--win", "#34c759"),
      loss: read("--loss", "#ff5a4e"),
      violet: read("--chart-4", "#a78bfa"),
      surface: read("--surface-2", "#16161b"),
      surface3: read("--surface-3", "#1d1d24"),
      line: read("--line", "rgba(255,255,255,.08)"),
      font: read(
        "--font-sans",
        "'Inter', -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      ),
      mono: read("--font-mono", "'JetBrains Mono', ui-monospace, monospace"),
    };
  }

  const Chart = window.Chart;
  let cached = readTokens();

  window.courtsideChartColors = () => (cached = readTokens());

  function applyDefaults(t) {
    Chart.defaults.font.family = t.font;
    Chart.defaults.font.size = 12;
    Chart.defaults.color = t.text;
    Chart.defaults.borderColor = t.grid;

    const labels = Chart.defaults.plugins.legend.labels;
    labels.usePointStyle = true;
    labels.pointStyle = "circle";
    labels.boxWidth = 7;
    labels.boxHeight = 7;
    labels.padding = 14;
    labels.color = t.text;

    const tooltip = Chart.defaults.plugins.tooltip;
    tooltip.backgroundColor = t.surface;
    tooltip.titleColor = t.ink;
    tooltip.bodyColor = t.text;
    tooltip.borderColor = t.line;
    tooltip.borderWidth = 1;
    tooltip.padding = 10;
    tooltip.cornerRadius = 10;
    tooltip.boxWidth = 7;
    tooltip.boxHeight = 7;
    tooltip.usePointStyle = true;
    tooltip.titleFont = { size: 13, weight: "600" };
    tooltip.bodyFont = { size: 12 };
  }

  applyDefaults(cached);

  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation = { duration: 520, easing: "easeOutQuart" };

  // Themes the axes of charts created by the page scripts without touching
  // their markup: defaults are merged in, explicit options still win.
  window.Chart = new Proxy(Chart, {
    construct(target, args) {
      const t = (cached = readTokens());
      applyDefaults(t);

      const [ctx, config = {}, ...rest] = args;
      config.options = config.options || {};
      if (config.options.responsive === undefined) config.options.responsive = true;
      if (config.options.maintainAspectRatio === undefined) config.options.maintainAspectRatio = false;

      config.options.scales = config.options.scales || {};
      Object.values(config.options.scales).forEach((scale) => {
        scale = scale || {};
        scale.grid = Object.assign({ color: t.grid, drawBorder: false, borderColor: t.grid }, scale.grid);
        scale.ticks = Object.assign({ color: t.muted, font: { size: 12 } }, scale.ticks);
        if (scale.border && scale.border.color === undefined) scale.border.color = t.grid;
      });

      config.options.plugins = config.options.plugins || {};
      if (config.options.plugins.legend && config.options.plugins.legend.labels) {
        config.options.plugins.legend.labels = Object.assign(
          { usePointStyle: true, pointStyle: "circle", boxWidth: 7, boxHeight: 7, padding: 14, color: t.text },
          config.options.plugins.legend.labels,
        );
      }

      return new target(ctx, config, ...rest);
    },
  });
})();
