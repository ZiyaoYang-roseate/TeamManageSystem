// Verifies the league-admin read-only view: POST to /salary and /scouting must be
// rejected with a 303 redirect back to the same page, and the pages must render
// the read-only hint instead of write forms.
import { readFileSync } from "node:fs";

const BASE = "http://127.0.0.1:5000";
const USER = { username: "league_admin", password: "123456" };

function pickCookie(response) {
  const raw = response.headers.getSetCookie ? response.headers.getSetCookie() : [];
  return raw.map((c) => c.split(";")[0]).join("; ");
}

async function login() {
  const page = await fetch(`${BASE}/login`, { redirect: "manual" });
  const html = await page.text();
  const token = (html.match(/name="_csrf_token"\s+value="([^"]+)"/) || [])[1];
  const cookie = pickCookie(page);
  const body = new URLSearchParams({
    username: USER.username,
    password: USER.password,
    role: "league_admin",
    _csrf_token: token,
  });
  const res = await fetch(`${BASE}/login`, {
    method: "POST",
    body,
    headers: { "content-type": "application/x-www-form-urlencoded", cookie },
    redirect: "manual",
  });
  return { cookie: pickCookie(res) || cookie, status: res.status };
}

async function getToken(cookie, path) {
  const res = await fetch(`${BASE}${path}`, { headers: { cookie }, redirect: "manual" });
  const html = await res.text();
  return {
    status: res.status,
    token: (html.match(/name="_csrf_token"\s+value="([^"]+)"/) || [])[1],
    html,
  };
}

const session = await login();
console.log(`login -> ${session.status}`);

for (const path of ["/salary", "/scouting"]) {
  const page = await getToken(session.cookie, path);
  const readonly = page.html.includes("只读查看");
  const writeForm = page.html.includes("生成组合方案") || page.html.includes("AI 推荐候选人");
  const post = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", cookie: session.cookie },
    body: new URLSearchParams({ _csrf_token: page.token || "", action: "noop" }),
    redirect: "manual",
  });
  const location = post.headers.get("location") || "";
  console.log(
    `${path}: GET ${page.status} readonly=${readonly} writeForm=${writeForm} POST ${post.status} -> ${location}`,
  );
}
