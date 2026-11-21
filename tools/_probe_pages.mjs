// Smoke-check every page of the running Flask app: logs in as each role and
// reports status codes plus the first lines of any traceback.
const BASE = "http://127.0.0.1:5000";

function cookieFrom(response) {
  const raw = response.headers.getSetCookie ? response.headers.getSetCookie() : [];
  return raw.map((entry) => entry.split(";")[0]).join("; ");
}

async function login(username, password, role) {
  const page = await fetch(`${BASE}/login`);
  const html = await page.text();
  const cookie = cookieFrom(page);
  const token = html.match(/name="_csrf_token"\s+value="([^"]+)"/)?.[1] ?? "";

  const body = new URLSearchParams({ username, password, role, _csrf_token: token });
  const response = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", cookie },
    body,
    redirect: "manual",
  });
  return { status: response.status, cookie: cookieFrom(response) || cookie, token };
}

async function check(session, path) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { cookie: session.cookie },
    redirect: "manual",
  });
  const text = response.status === 200 ? await response.text() : "";
  const title = text.match(/<title>([^<]*)<\/title>/i)?.[1] ?? "";
  const error = text.match(/<pre[^>]*>([\s\S]{0,400})/);
  return {
    path,
    status: response.status,
    location: response.headers.get("location") || "",
    title,
    details: error ? error[1].replace(/\s+/g, " ").slice(0, 300) : "",
  };
}

const pages = {
  league_admin: [
    "/", "/league", "/analytics", "/shot-chart", "/lineups", "/compare",
    "/players", "/players/add", "/players/1", "/players/7", "/teams", "/teams/1", "/teams/add", "/salary",
    "/scouting", "/games", "/games/1", "/games/1/play-by-play", "/health",
    "/games/1/analysis/scout", "/games/1/analysis/review",
  ],
  management: [
    "/", "/my-team", "/analytics", "/shot-chart", "/lineups", "/compare", "/players",
    "/salary", "/free-agency", "/trade-machine", "/trade-proposals", "/scouting",
    "/games", "/games/1", "/health",
  ],
  scorekeeper: ["/", "/scorekeeper/calendar", "/scorekeeper/game/1"],
};

const users = {
  league_admin: ["league_admin", "123456"],
  management: ["gm_lal", "123456"],
  scorekeeper: ["scorekeeper", "123456"],
};

for (const [role, paths] of Object.entries(pages)) {
  const [username, password] = users[role];
  const session = await login(username, password, role);
  console.log(`\n== ${role} (login ${session.status}) token=${session.token ? "yes" : "no"} ==`);
  for (const path of paths) {
    const result = await check(session, path);
    const flag = result.status === 200 ? "ok  " : "FAIL";
    console.log(
      `${flag} ${String(result.status).padEnd(3)} ${path.padEnd(26)} ${result.location || result.title} ${result.details}`,
    );
  }
}

