// Capture full-page screenshots of the running Flask app through the Chrome
// DevTools Protocol (no Playwright/Puppeteer available in this environment).
//
//   node tools/_shots.mjs [pageFilter]
//
// Screenshots land in _shots/<role>/<slug>.png
import { spawn } from "node:child_process";
import { mkdir, writeFile, rm } from "node:fs/promises";
import path from "node:path";

const BASE = "http://127.0.0.1:5000";
const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const PORT = 9333;
const OUT = path.resolve("_shots");
const VIEWPORT = { width: 1440, height: 1000 };

const PAGES = [
  { role: "league_admin", slug: "login", path: "/login?intro=0", anonymous: true },
  { role: "league_admin", slug: "intro", path: "/login?intro=1", anonymous: true },
  { role: "league_admin", slug: "home", path: "/" },
  { role: "league_admin", slug: "league", path: "/league" },
  { role: "league_admin", slug: "analytics", path: "/analytics" },
  { role: "league_admin", slug: "shot-chart", path: "/shot-chart" },
  { role: "league_admin", slug: "players", path: "/players" },
  { role: "league_admin", slug: "player", path: "/players/1" },
  { role: "league_admin", slug: "teams", path: "/teams" },
  { role: "league_admin", slug: "team", path: "/teams/1" },
  { role: "league_admin", slug: "salary", path: "/salary" },
  { role: "league_admin", slug: "lineups", path: "/lineups" },
  { role: "league_admin", slug: "compare", path: "/compare" },
  { role: "league_admin", slug: "scouting", path: "/scouting" },
  { role: "league_admin", slug: "salary-team", path: "/salary?team_id=2" },
  { role: "league_admin", slug: "forbidden", path: "/games/1/analysis/scout" },
  { role: "league_admin", slug: "not-found", path: "/no-such-page" },
  { role: "league_admin", slug: "games", path: "/games" },
  { role: "league_admin", slug: "game", path: "/games/1" },
  { role: "league_admin", slug: "play-by-play", path: "/games/1/play-by-play" },
  { role: "league_admin", slug: "analysis-scout", path: "/games/1/analysis/scout" },
  { role: "league_admin", slug: "analysis-review", path: "/games/1/analysis/review" },
  { role: "league_admin", slug: "health", path: "/health" },
  { role: "management", slug: "my-team", path: "/my-team" },
  { role: "management", slug: "free-agency", path: "/free-agency" },
  { role: "management", slug: "trade-machine", path: "/trade-machine" },
  { role: "management", slug: "trade-proposals", path: "/trade-proposals" },
  { role: "scorekeeper", slug: "calendar", path: "/scorekeeper/calendar" },
  { role: "scorekeeper", slug: "scorekeeper-game", path: "/scorekeeper/game/1" },
];

const USERS = {
  league_admin: ["league_admin", "123456"],
  management: ["gm_lal", "123456"],
  scorekeeper: ["scorekeeper", "123456"],
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function cookieFrom(response) {
  const raw = response.headers.getSetCookie ? response.headers.getSetCookie() : [];
  return Object.fromEntries(
    raw.map((entry) => {
      const [pair] = entry.split(";");
      const index = pair.indexOf("=");
      return [pair.slice(0, index), pair.slice(index + 1)];
    }),
  );
}

async function login(role) {
  const [username, password] = USERS[role];
  const page = await fetch(`${BASE}/login`);
  const html = await page.text();
  const cookies = cookieFrom(page);
  const token = html.match(/name="_csrf_token"\s+value="([^"]+)"/)?.[1] ?? "";
  const response = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", cookie: new URLSearchParams(cookies).toString() },
    body: new URLSearchParams({ username, password, role, _csrf_token: token }),
    redirect: "manual",
  });
  const next = cookieFrom(response);
  return next.session || cookies.session;
}

/* --------------------------------------------------------------- CDP client */
class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve);
      this.socket.addEventListener("error", reject);
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(JSON.stringify(message.error)));
        else resolve(message.result);
      } else if (message.method) {
        (this.events.get(message.method) || []).forEach((handler) => handler(message.params));
      }
    });
  }

  send(method, params = {}, sessionId) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    this.socket.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }

  once(method) {
    return new Promise((resolve) => {
      const handler = (params) => {
        const list = this.events.get(method) || [];
        this.events.set(method, list.filter((entry) => entry !== handler));
        resolve(params);
      };
      this.events.set(method, [...(this.events.get(method) || []), handler]);
    });
  }

  close() {
    this.socket.close();
  }
}

async function waitForDevtools() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (response.ok) return (await response.json()).webSocketDebuggerUrl;
    } catch (error) {
      /* keep polling while chrome boots */
    }
    await sleep(250);
  }
  throw new Error("chrome devtools did not start");
}

/* -------------------------------------------------------------------- main */
const filter = process.argv[2];
const pages = filter ? PAGES.filter((page) => page.slug.includes(filter) || page.path.includes(filter)) : PAGES;

const profileDir = path.resolve("_shots/.chrome-profile");
await rm(profileDir, { recursive: true, force: true });
await mkdir(OUT, { recursive: true });

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profileDir}`,
    `--window-size=${VIEWPORT.width},${VIEWPORT.height}`,
    "about:blank",
  ],
  { stdio: "ignore" },
);

try {
  const wsUrl = await waitForDevtools();
  const cdp = new Cdp(wsUrl);
  await cdp.ready;

  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });

  await cdp.send("Page.enable", {}, sessionId);
  await cdp.send("Network.enable", {}, sessionId);
  await cdp.send(
    "Emulation.setDeviceMetricsOverride",
    { width: VIEWPORT.width, height: VIEWPORT.height, deviceScaleFactor: 1, mobile: false },
    sessionId,
  );

  const sessions = {};
  for (const role of Object.keys(USERS)) sessions[role] = await login(role);
  console.log("logged in:", Object.keys(sessions).join(", "));

  for (const page of pages) {
    await cdp.send(
      "Emulation.setDeviceMetricsOverride",
      { width: VIEWPORT.width, height: VIEWPORT.height, deviceScaleFactor: 1, mobile: false },
      sessionId,
    );
    await cdp.send("Network.clearBrowserCookies", {}, sessionId);
    if (!page.anonymous) {
      const value = sessions[page.role];
      if (!value) throw new Error(`missing session for ${page.role}`);
      await cdp.send(
        "Network.setCookie",
        { name: "session", value, domain: "127.0.0.1", path: "/", httpOnly: true },
        sessionId,
      );
    }

    const loaded = cdp.once("Page.loadEventFired");
    await cdp.send("Page.navigate", { url: `${BASE}${page.path}` }, sessionId);
    await Promise.race([loaded, sleep(8000)]);
    await sleep(900); // fonts, chart animation and skeleton fade

    // Wait for entrance animations to settle so screenshots are not mid-fade.
    for (let attempt = 0; attempt < 24; attempt += 1) {
      const state = await cdp.send(
        "Runtime.evaluate",
        {
          expression: "(function(){var n=document.querySelectorAll('[data-reveal]');for(var i=0;i<n.length;i++){if(!n[i].classList.contains('is-in'))return 'pending';}return 'ready';})()",
          returnByValue: true,
        },
        sessionId,
      );
      if (state.result && state.result.value === "ready") break;
      await sleep(120);
    }
    await sleep(260);

    // Grow the viewport to the full document first: resizing during capture makes
    // responsive charts restart their animation, which captured half-drawn lines.
    const size = await cdp.send(
      "Runtime.evaluate",
      {
        expression: "JSON.stringify({h: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)})",
        returnByValue: true,
      },
      sessionId,
    );
    const fullHeight = Math.min(20000, JSON.parse(size.result.value).h);
    await cdp.send(
      "Emulation.setDeviceMetricsOverride",
      { width: VIEWPORT.width, height: fullHeight, deviceScaleFactor: 1, mobile: false },
      sessionId,
    );
    await sleep(300);

    // Force lazy images below the fold to decode before capture.
    await cdp.send(
      "Runtime.evaluate",
      {
        expression: "(function(){var imgs=document.querySelectorAll('img[loading=\"lazy\"]');for(var i=0;i<imgs.length;i++){imgs[i].loading='eager';var s=imgs[i].getAttribute('src');if(s)imgs[i].src=s;}return imgs.length;})()",
        returnByValue: true,
      },
      sessionId,
    );
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const pending = await cdp.send(
        "Runtime.evaluate",
        {
          expression: "(function(){var imgs=document.querySelectorAll('img');for(var i=0;i<imgs.length;i++){if(!imgs[i].complete||imgs[i].naturalWidth===0)return 1;}return 0;})()",
          returnByValue: true,
        },
        sessionId,
      );
      if (pending.result && pending.result.value === 0) break;
      await sleep(150);
    }
    await sleep(700);

    const shot = await cdp.send(
      "Page.captureScreenshot",
      { format: "png", fromSurface: true },
      sessionId,
    );
    const directory = path.join(OUT, page.role);
    await mkdir(directory, { recursive: true });
    const file = path.join(directory, `${page.slug}.png`);
    await writeFile(file, Buffer.from(shot.data, "base64"));
    const { result } = await cdp.send(
      "Runtime.evaluate",
      { expression: "JSON.stringify({w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight})", returnByValue: true },
      sessionId,
    );
    console.log(`${file}  ${result.value}`);
  }

  cdp.close();
} finally {
  chrome.kill();
}
