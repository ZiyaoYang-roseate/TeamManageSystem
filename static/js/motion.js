/* ==========================================================================
   Courtside · motion runtime
   - scroll reveal + count-up + parallax + tilt
   - topbar scroll state
   - gallery lightbox
   - game/schedule dialog (data-game JSON)
   - cinematic intro ("演出") for the sign-in screen
   No dependencies. Gracefully degrades without JS.
   ========================================================================== */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  function parseJSON(value, fallback) {
    if (!value) return fallback;
    try { return JSON.parse(value); } catch (error) { return fallback; }
  }

  /* ── Scroll reveal ───────────────────────────────────────────────────── */
  function initReveal() {
    var nodes = Array.prototype.slice.call(document.querySelectorAll("[data-reveal]"));
    if (!nodes.length) return;

    if (reduceMotion || !("IntersectionObserver" in window)) {
      nodes.forEach(function (node) { node.classList.add("is-in"); });
      return;
    }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });

    nodes.forEach(function (node) { observer.observe(node); });
  }

  /* ── Count-up numbers ────────────────────────────────────────────────── */
  function initCountUp() {
    var nodes = Array.prototype.slice.call(document.querySelectorAll("[data-countup]"));
    if (!nodes.length) return;

    function format(value, decimals) {
      return decimals > 0 ? value.toFixed(decimals) : String(Math.round(value));
    }

    function run(node) {
      var target = parseFloat(node.getAttribute("data-countup"));
      if (!isFinite(target)) return;
      var decimals = parseInt(node.getAttribute("data-decimals") || "0", 10);
      var prefix = node.getAttribute("data-prefix") || "";
      var suffix = node.getAttribute("data-suffix") || "";

      if (reduceMotion) {
        node.textContent = prefix + format(target, decimals) + suffix;
        return;
      }

      var start = null;
      var duration = 900;

      function step(timestamp) {
        if (start === null) start = timestamp;
        var progress = Math.min((timestamp - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        node.textContent = prefix + format(target * eased, decimals) + suffix;
        if (progress < 1) window.requestAnimationFrame(step);
      }

      window.requestAnimationFrame(step);
    }

    if (!("IntersectionObserver" in window)) { nodes.forEach(run); return; }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        run(entry.target);
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.4 });

    nodes.forEach(function (node) { observer.observe(node); });
  }

  /* ── Scroll parallax ─────────────────────────────────────────────────── */
  function initParallax() {
    var nodes = Array.prototype.slice.call(document.querySelectorAll("[data-parallax]"));
    if (!nodes.length || reduceMotion) return;

    var ticking = false;

    function update() {
      ticking = false;
      var viewport = window.innerHeight;
      nodes.forEach(function (node) {
        var speed = parseFloat(node.getAttribute("data-parallax")) || 0.12;
        var rect = node.getBoundingClientRect();
        if (rect.bottom < -200 || rect.top > viewport + 200) return;
        var offset = (rect.top + rect.height / 2 - viewport / 2) * speed;
        node.style.transform = "translate3d(0," + offset.toFixed(2) + "px,0)";
      });
    }

    function request() {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(update);
    }

    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", request);
    update();
  }

  /* ── Pointer parallax (hero, login visual) ───────────────────────────── */
  function initPointerParallax() {
    var stages = Array.prototype.slice.call(document.querySelectorAll("[data-pointer-parallax]"));
    if (!stages.length || reduceMotion) return;

    stages.forEach(function (stage) {
      var layers = Array.prototype.slice.call(stage.querySelectorAll("[data-depth]"));

      stage.addEventListener("pointermove", function (event) {
        var rect = stage.getBoundingClientRect();
        var x = (event.clientX - rect.left) / rect.width - 0.5;
        var y = (event.clientY - rect.top) / rect.height - 0.5;
        layers.forEach(function (layer) {
          var depth = parseFloat(layer.getAttribute("data-depth")) || 8;
          layer.style.transform =
            "translate3d(" + (-x * depth).toFixed(2) + "px," + (-y * depth).toFixed(2) + "px,0)";
        });
      });

      stage.addEventListener("pointerleave", function () {
        layers.forEach(function (layer) { layer.style.transform = ""; });
      });
    });
  }

  /* ── Tilt cards ──────────────────────────────────────────────────────── */
  function initTilt() {
    if (reduceMotion) return;
    var cards = Array.prototype.slice.call(document.querySelectorAll(".tilt"));
    cards.forEach(function (card) {
      card.addEventListener("pointermove", function (event) {
        var rect = card.getBoundingClientRect();
        var px = (event.clientX - rect.left) / rect.width - 0.5;
        var py = (event.clientY - rect.top) / rect.height - 0.5;
        card.style.setProperty("--ry", (px * 7).toFixed(2) + "deg");
        card.style.setProperty("--rx", (-py * 7).toFixed(2) + "deg");
      });
      card.addEventListener("pointerleave", function () {
        card.style.setProperty("--ry", "0deg");
        card.style.setProperty("--rx", "0deg");
      });
    });
  }

  /* ── Topbar scroll state ─────────────────────────────────────────────── */
  function initTopbar() {
    var bar = document.querySelector(".topbar");
    if (!bar) return;

    var progress = document.createElement("div");
    progress.className = "scroll-progress";
    progress.setAttribute("aria-hidden", "true");
    document.body.appendChild(progress);

    var last = -1;
    var ticking = false;

    function paintProgress() {
      var doc = document.documentElement;
      var max = doc.scrollHeight - window.innerHeight;
      var ratio = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
      progress.style.transform = "scaleX(" + ratio.toFixed(4) + ")";
    }

    function update() {
      var scrolled = window.scrollY > 8;
      if (scrolled !== last) {
        last = scrolled;
        bar.classList.toggle("is-scrolled", scrolled);
      }
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(function () {
          paintProgress();
          ticking = false;
        });
      }
    }

    paintProgress();

    window.addEventListener("scroll", update, { passive: true });
    update();
  }

  /* ── Gallery lightbox ────────────────────────────────────────────────── */
  function initGallery() {
    var items = Array.prototype.slice.call(document.querySelectorAll("[data-gallery-item]"));
    if (!items.length) return;

    var box = document.getElementById("lightbox");
    if (!box) {
      box = document.createElement("div");
      box.id = "lightbox";
      box.className = "lightbox";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.innerHTML =
        '<button type="button" class="lightbox-close" aria-label="关闭">✕</button>' +
        '<button type="button" class="lightbox-nav prev" aria-label="上一张">←</button>' +
        '<img alt="">' +
        '<button type="button" class="lightbox-nav next" aria-label="下一张">→</button>' +
        '<div class="lightbox-counter"></div>';
      document.body.appendChild(box);
    }

    var image = box.querySelector("img");
    var counter = box.querySelector(".lightbox-counter");
    var current = 0;

    function show(index) {
      current = (index + items.length) % items.length;
      var item = items[current];
      image.src = item.getAttribute("data-src") || "";
      image.alt = item.getAttribute("data-label") || "";
      counter.textContent = (current + 1) + " / " + items.length;
      box.classList.add("is-open");
      document.body.style.overflow = "hidden";
    }

    function close() {
      box.classList.remove("is-open");
      document.body.style.overflow = "";
    }

    items.forEach(function (item, index) {
      item.addEventListener("click", function () { show(index); });
    });

    box.querySelector(".lightbox-close").addEventListener("click", close);
    box.querySelector(".lightbox-nav.prev").addEventListener("click", function (event) { event.stopPropagation(); show(current - 1); });
    box.querySelector(".lightbox-nav.next").addEventListener("click", function (event) { event.stopPropagation(); show(current + 1); });
    box.addEventListener("click", function (event) { if (event.target === box) close(); });

    document.addEventListener("keydown", function (event) {
      if (!box.classList.contains("is-open")) return;
      if (event.key === "Escape") close();
      if (event.key === "ArrowLeft") show(current - 1);
      if (event.key === "ArrowRight") show(current + 1);
    });
  }

  /* ── Game dialog (赛程弹窗) ──────────────────────────────────────────── */
  var gameModal = null;

  function ensureGameModal() {
    if (gameModal) return gameModal;
    gameModal = document.getElementById("gameModal");
    if (!gameModal) {
      gameModal = document.createElement("div");
      gameModal.id = "gameModal";
      gameModal.className = "game-modal";
      gameModal.setAttribute("role", "dialog");
      gameModal.setAttribute("aria-modal", "true");
      gameModal.innerHTML = '<div class="game-dialog" id="gameDialog"></div>';
      document.body.appendChild(gameModal);

      gameModal.addEventListener("click", function (event) {
        if (event.target === gameModal || event.target.closest("[data-game-close]")) closeGame();
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && gameModal.classList.contains("is-open")) closeGame();
      });
    }
    return gameModal;
  }

  function closeGame() {
    if (!gameModal) return;
    gameModal.classList.remove("is-open");
    document.body.style.overflow = "";
  }

  function teamBadge(abbr, isWinner) {
    return '<span class="badge-team">' + escapeHTML(abbr || "—") + "</span>";
  }

  function escapeHTML(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }

  function openGame(data) {
    var modal = ensureGameModal();
    var dialog = modal.querySelector("#gameDialog");
    var home = data.home || {};
    var away = data.away || {};
    var live = data.status === "live";
    var finished = data.status === "finished";
    var homeWin = finished && Number(data.home_score) > Number(data.away_score);
    var awayWin = finished && Number(data.away_score) > Number(data.home_score);
    var scoreLine = finished || live
      ? '<span class="' + (awayWin ? "" : "gd-dim") + '">' + escapeHTML(data.away_score) + "</span>" +
        '<span class="sep">:</span>' +
        '<span class="' + (homeWin ? "" : "gd-dim") + '">' + escapeHTML(data.home_score) + "</span>"
      : '<span class="sep">VS</span>';

    var statusLabel = live ? "进行中" : finished ? "已结束" : "待开始";
    var rows = "";
    if (data.date) rows += '<div class="gd-row"><span class="k">比赛日</span><span class="v">' + escapeHTML(data.date) + "</span></div>";
    if (data.venue) rows += '<div class="gd-row"><span class="k">场馆</span><span class="v">' + escapeHTML(data.venue) + "</span></div>";
    if (data.season) rows += '<div class="gd-row"><span class="k">赛季</span><span class="v">' + escapeHTML(data.season) + "</span></div>";
    if (data.margin != null && finished) rows += '<div class="gd-row"><span class="k">分差</span><span class="v">' + escapeHTML(data.margin) + "</span></div>";

    dialog.innerHTML =
      '<div class="game-dialog-head">' +
        '<div><div class="kicker">' + escapeHTML(data.kicker || "Game detail") + "</div>" +
        '<div class="title">' + escapeHTML(away.name || "客队") + " @ " + escapeHTML(home.name || "主队") + "</div></div>" +
        '<button type="button" class="game-dialog-close" data-game-close aria-label="关闭">✕</button>' +
      "</div>" +
      '<div class="game-dialog-body">' +
        '<div class="gd-matchup">' +
          '<div class="gd-team gd-away' + (awayWin ? " is-winner" : "") + '">' + teamBadge(away.abbr) +
            '<div class="name">' + escapeHTML(away.name || "客队") + "</div>" +
            '<div class="sub">客场 · ' + escapeHTML(away.abbr || "") + "</div></div>" +
          '<div><div class="gd-score">' + scoreLine + "</div>" +
            '<div class="gd-status ' + (live ? "is-live" : "") + '">' + statusLabel + "</div></div>" +
          '<div class="gd-team gd-home' + (homeWin ? " is-winner" : "") + '">' + teamBadge(home.abbr) +
            '<div class="name">' + escapeHTML(home.name || "主队") + "</div>" +
            '<div class="sub">主场 · ' + escapeHTML(home.abbr || "") + "</div></div>" +
        "</div>" +
        (rows ? '<div class="gd-rows">' + rows + "</div>" : "") +
        '<div class="gd-actions">' +
          (data.href ? '<a class="btn btn-primary btn-sm" href="' + escapeHTML(data.href) + '"><i class="bi bi-bar-chart-line me-1"></i>技术统计</a>' : "") +
          (data.play_by_play ? '<a class="btn btn-secondary btn-sm" href="' + escapeHTML(data.play_by_play) + '"><i class="bi bi-list-ol me-1"></i>逐回合</a>' : "") +
          (data.analysis ? '<a class="btn btn-outline-secondary btn-sm" href="' + escapeHTML(data.analysis) + '"><i class="bi bi-stars me-1"></i>赛后复盘</a>' : "") +
        "</div>" +
      "</div>";

    modal.classList.add("is-open");
    document.body.style.overflow = "hidden";
    var closeButton = dialog.querySelector(".game-dialog-close");
    if (closeButton) closeButton.focus({ preventScroll: true });
  }

  function initGameModal() {
    document.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-game]");
      if (!trigger) return;
      var data = parseJSON(trigger.getAttribute("data-game"), null);
      if (!data) return;
      event.preventDefault();
      openGame(data);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      var trigger = event.target.closest && event.target.closest("[data-game]");
      if (!trigger || trigger.tagName === "BUTTON" || trigger.tagName === "A") return;
      var data = parseJSON(trigger.getAttribute("data-game"), null);
      if (!data) return;
      event.preventDefault();
      openGame(data);
    });
  }

  /* ── Cinematic intro ─────────────────────────────────────────────────── */
  function initIntro() {
    var intro = document.getElementById("intro");
    if (!intro) return;

    var video = intro.querySelector("video");
    var shoot = intro.querySelector("[data-intro-shoot]");
    var skip = intro.querySelector("[data-intro-skip]");
    var progress = intro.querySelector(".intro-progress i");
    var storageKey = "courtside.intro.seen";
    var force = /(?:\?|&)intro=1(?:&|$)/.test(window.location.search);
    var off = /(?:\?|&)intro=0(?:&|$)/.test(window.location.search);

    if (off) {
      intro.classList.add("is-hidden");
      return;
    }

    function remember() {
      try { window.sessionStorage.setItem(storageKey, "1"); } catch (error) { /* ignore */ }
    }

    function seen() {
      try { return window.sessionStorage.getItem(storageKey) === "1"; } catch (error) { return false; }
    }

    function finish() {
      intro.classList.add("is-done");
      remember();
      window.setTimeout(function () {
        intro.classList.add("is-leaving");
        document.body.style.overflow = "";
        window.setTimeout(function () { intro.classList.add("is-hidden"); }, 620);
      }, 520);
    }

    if (seen() && !force) {
      intro.classList.add("is-hidden");
      return;
    }

    document.body.style.overflow = "hidden";
    var readyTimer = window.setTimeout(function () { intro.classList.add("is-ready"); }, 1400);

    if (video) {
      video.addEventListener("loadeddata", function () { intro.classList.add("is-ready"); window.clearTimeout(readyTimer); });
      video.addEventListener("canplay", function () { intro.classList.add("is-ready"); window.clearTimeout(readyTimer); });
      video.addEventListener("timeupdate", function () {
        if (!progress || !video.duration) return;
        progress.style.width = ((video.currentTime / video.duration) * 100).toFixed(2) + "%";
      });
      video.addEventListener("ended", finish);
    } else {
      intro.classList.add("is-ready");
    }

    if (shoot) {
      shoot.addEventListener("click", function () {
        intro.classList.add("is-playing");
        if (video) {
          var promise = video.play();
          if (promise && promise.catch) promise.catch(function () { finish(); });
        } else {
          window.setTimeout(finish, 900);
        }
      });
    }

    if (skip) skip.addEventListener("click", finish);

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !intro.classList.contains("is-hidden")) finish();
    });
  }

  /* ── Public helpers ──────────────────────────────────────────────────── */
  window.CourtsideMotion = {
    openGame: openGame,
    closeGame: closeGame,
    refresh: function () { initReveal(); initCountUp(); }
  };

  ready(function () {
    initReveal();
    initCountUp();
    initParallax();
    initPointerParallax();
    initTilt();
    initTopbar();
    initGallery();
    initGameModal();
    initIntro();
  });
})();
