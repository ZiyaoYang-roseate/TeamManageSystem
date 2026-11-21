/* ============================================================================
   COURTSIDE · application shell behaviour
   ----------------------------------------------------------------------------
   Framework-free on purpose: the Flask/Jinja pages only need a handful of
   global behaviours (theme, sidebar, season context, search, tooltips).
   Chart.js theming lives in static/js/charts-init.js, loaded in <head>.
   ========================================================================== */
(function () {
  "use strict";

  const html = document.documentElement;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  /* ---------------------------------------------------------------- theme */
  const themeToggle = $("#themeToggle");

  function syncThemeIcon() {
    if (!themeToggle) return;
    const icon = themeToggle.querySelector("i");
    if (!icon) return;
    const isDark = html.getAttribute("data-bs-theme") === "dark";
    icon.className = isDark ? "bi bi-sun" : "bi bi-moon-stars";
    themeToggle.setAttribute("title", isDark ? "切换到浅色主题" : "切换到深色主题");
  }

  function applyTheme(theme) {
    html.setAttribute("data-bs-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch (error) {
      /* storage may be unavailable in private mode — the theme still applies */
    }
    syncThemeIcon();
    document.dispatchEvent(new CustomEvent("themechange", { detail: { theme } }));
  }

  themeToggle?.addEventListener("click", () => {
    applyTheme(html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
  });

  syncThemeIcon();

  /* -------------------------------------------------------------- sidebar */
  const sidebar = $("#sidebar");
  const mobileMenuToggle = $("#mobileMenuToggle");
  const backdrop = $("#sidebarBackdrop");

  function setSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("show", open);
    backdrop?.classList.toggle("show", open);
    mobileMenuToggle?.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.style.overflow = open ? "hidden" : "";
  }

  mobileMenuToggle?.addEventListener("click", () => setSidebar(!sidebar.classList.contains("show")));
  backdrop?.addEventListener("click", () => setSidebar(false));

  sidebar?.addEventListener("click", (event) => {
    if (event.target.closest("a.nav-link") && window.matchMedia("(max-width: 991.98px)").matches) {
      setSidebar(false);
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebar?.classList.contains("show")) setSidebar(false);
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 991.98 && sidebar?.classList.contains("show")) setSidebar(false);
  });

  /* -------------------------------------------------- collapsible groups */
  function syncSidebarAccordions() {
    $$(".sidebar-dropdown").forEach((button) => {
      const targetId = button.getAttribute("data-sidebar-target");
      const target = targetId ? document.getElementById(targetId.slice(1)) : null;
      if (!target) return;
      const expanded = target.classList.contains("show");
      button.classList.toggle("collapsed", !expanded);
      button.setAttribute("aria-expanded", expanded ? "true" : "false");
    });
  }

  function toggleSidebarAccordion(target, willShow) {
    const startHeight = target.getBoundingClientRect().height;
    const contentHeight = target.scrollHeight;
    target.classList.toggle("show", willShow);
    target.style.height = `${startHeight}px`;

    const animation = target.animate(
      [{ height: `${startHeight}px` }, { height: `${willShow ? contentHeight : 0}px` }],
      { duration: 360, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
    );

    animation.onfinish = () => {
      target.style.height = willShow ? "auto" : "0px";
    };
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".sidebar-dropdown");
    if (!button) return;
    const targetId = button.getAttribute("data-sidebar-target");
    const target = targetId ? document.getElementById(targetId.slice(1)) : null;
    if (!target) return;
    toggleSidebarAccordion(target, !target.classList.contains("show"));
    syncSidebarAccordions();
  });

  syncSidebarAccordions();

  /* ------------------------------------------------- season + search ctx */
  const SEASON_KEY = "season";
  const DEFAULT_SEASON = "2025-26";

  function readSeason() {
    return new URLSearchParams(window.location.search).get("season")
      || localStorage.getItem(SEASON_KEY)
      || DEFAULT_SEASON;
  }

  function changeSeason(season) {
    try {
      localStorage.setItem(SEASON_KEY, season);
    } catch (error) {
      /* ignore */
    }
    const url = new URL(window.location.href);
    url.searchParams.set("season", season);
    window.location.href = url.toString();
  }

  window.changeSeason = changeSeason;

  const seasonSelect = $("#seasonSelect");
  if (seasonSelect) seasonSelect.value = readSeason();

  const sidebarSeason = $("#sidebarSeason");
  if (sidebarSeason) sidebarSeason.textContent = readSeason();

  function applySeasonToLinks() {
    const season = readSeason();
    $$("a[href]").forEach((link) => {
      const rawHref = link.getAttribute("href");
      if (!rawHref || rawHref.startsWith("http") || rawHref.startsWith("#") || rawHref.startsWith("mailto:")) return;
      if (link.hasAttribute("data-no-season")) return;
      const url = new URL(rawHref, window.location.href);
      if (url.origin !== window.location.origin) return;
      url.searchParams.set("season", season);
      link.setAttribute("href", url.pathname + url.search + url.hash);
    });
  }

  applySeasonToLinks();

  const searchInput = $("#globalSearch");
  if (searchInput) {
    searchInput.value = new URLSearchParams(window.location.search).get("q") || "";

    searchInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const keyword = searchInput.value.trim();
      const url = new URL(window.location.href);
      if (keyword) url.searchParams.set("q", keyword);
      else url.searchParams.delete("q");
      url.searchParams.set("page", "1");
      window.location.href = url.toString();
    });
  }

  // "/" focuses the global search, matching the kbd hint in the topbar.
  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (event.key === "/" && !["input", "textarea", "select"].includes(tag) && searchInput) {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
  });

  /* ------------------------------------------------------ client filtering */
  const keyword = new URLSearchParams(window.location.search).get("q") || "";
  if (keyword && !document.getElementById("serverSearchScope")) {
    const needle = keyword.toLowerCase();
    $$("table tbody tr").forEach((row) => {
      row.style.display = row.textContent.toLowerCase().includes(needle) ? "" : "none";
    });
  }

  /* ------------------------------------------------------------- bootstrap */
  if (window.bootstrap) {
    $$('[data-bs-toggle="tooltip"]').forEach((element) => new bootstrap.Tooltip(element));
  }

  /* --------------------------------------------------------------- loader */
  window.addEventListener("load", () => {
    const skeleton = document.getElementById("pageSkeleton");
    if (!skeleton) return;
    skeleton.classList.add("is-hidden");
    setTimeout(() => skeleton.remove(), 320);
  });
})();
