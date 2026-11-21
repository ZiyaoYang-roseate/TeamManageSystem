// Validates that every data-game attribute on the rendered pages is valid JSON
// and carries the keys the schedule dialog needs.
const BASE = "http://127.0.0.1:5000";

function cookieFrom(response) {
  const raw = response.headers.getSetCookie ? response.headers.getSetCookie() : [];
  return Object.fromEntries(raw.map((entry) => entry.split(";")[0].split("=")));
}

async function login(username, password, role) {
  const first = await fetch(`${BASE}/login`, { redirect: "manual" });
  const jar = cookieFrom(first);
  const html = await first.text();
  const match = html.match(/name="_csrf_token"\s+value="([^"]+)"/);
  if (!match) throw new Error("no csrf token");
  const body = new URLSearchParams({ username, password, role, _csrf_token: match[1], next: "/" });
  const res = await fetch(`${BASE}/login`, {
    method: "POST",
    redirect: "manual",
    headers: { cookie: Object.entries(jar).map(([k, v]) => `${k}=${v}`).join("; "), "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  const merged = { ...jar, ...cookieFrom(res) };
  return Object.entries(merged).map(([k, v]) => `${k}=${v}`).join("; ");
}

function decodeAttr(value) {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&#34;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

const pages = ["/", "/games"];
const required = ["kicker", "status", "date", "home_score", "away_score", "home", "away", "href", "play_by_play"];

const jar = await login("league_admin", "123456", "league_admin");
let failures = 0;
for (const page of pages) {
  const res = await fetch(`${BASE}${page}`, { headers: { cookie: jar } });
  const html = await res.text();
  const attrs = [...html.matchAll(/data-game="([^"]*)"/g)].map((m) => decodeAttr(m[1]));
  let ok = 0;
  for (const raw of attrs) {
    try {
      const data = JSON.parse(raw);
      const missing = required.filter((key) => !(key in data));
      if (missing.length) throw new Error(`missing keys ${missing.join(",")}`);
      if (!data.home || !data.home.abbr || !data.away || !data.away.abbr) throw new Error("team payload incomplete");
      ok += 1;
    } catch (error) {
      failures += 1;
      console.log(`BAD ${page} :: ${error.message} :: ${raw.slice(0, 160)}`);
    }
  }
  console.log(`${page} -> ${ok}/${attrs.length} valid data-game payloads (status ${res.status})`);
}

console.log(failures === 0 ? "ALL GAME PAYLOADS VALID" : `${failures} INVALID PAYLOADS`);
process.exit(failures === 0 ? 0 : 1);
