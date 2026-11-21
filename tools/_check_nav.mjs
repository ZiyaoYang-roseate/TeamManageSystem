// Verifies role-gated chrome: scorekeepers must not see /compare links, and a
// league admin opening ?origin=league gets the "返回比赛处理" back link.
const BASE = "http://127.0.0.1:5000";

function cookies(res) {
  const list = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
  return list.map((c) => c.split(";")[0]).join("; ");
}

async function signIn(username, password, role) {
  const page = await fetch(`${BASE}/login`, { redirect: "manual" });
  const html = await page.text();
  const token = (html.match(/name="_csrf_token"\s+value="([^"]+)"/) || [])[1];
  const res = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", cookie: cookies(page) },
    body: new URLSearchParams({ username, password, role, _csrf_token: token }),
    redirect: "manual",
  });
  return cookies(res) || cookies(page);
}

const keeper = await signIn("scorekeeper", "123456", "scorekeeper");
for (const path of ["/analytics", "/players/1"]) {
  const html = await (await fetch(`${BASE}${path}`, { headers: { cookie: keeper } })).text();
  console.log(`keeper ${path}: compareLink=${html.includes('href="/compare')}`);
}

const admin = await signIn("league_admin", "123456", "league_admin");
for (const path of ["/games/1", "/games/1?origin=league"]) {
  const html = await (await fetch(`${BASE}${path}`, { headers: { cookie: admin } })).text();
  console.log(`admin ${path}: backToLeague=${html.includes("返回比赛处理")}`);
}
