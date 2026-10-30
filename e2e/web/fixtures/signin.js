// Signing in as a seed person (backend tools/seed.py): a fast path that goes straight to POST /v1/auth/exchange
// with the dev phone, and the real screens (phone, then the code 123456).
import { API, ORIGINS } from "./stack.js";

export const PEOPLE = {
  priya: "+919800001001", // customer, household set up
  captain: "+919800001004", // Meena: captain + customer
  kitchen: "+919800001005", // Lata: kitchen partner + customer
  rider: "+919800001006", // Arjun
  packer: "+919800001007", // Kavita
  hubManager: "+919800001008", // Vikram
  franchiseOwner: "+919800001009", // Sanjay
  ngo: "+919800001010", // Asha
  admin: "+919800001011", // Neha
  catalogEditor: "+919800001013", // Rekha
};
export const OTP_CODE = "123456";

const exchangeBody = (phone) => ({
  dev_phone: phone,
  device: { platform: "web", push_token: null },
});
const webHeaders = (origin) => ({
  "x-gb-client": "web",
  origin,
  "content-type": "application/json",
});

/** The console shows staff by their best role first (frontend/console/lib/session.js STAFF_ROLES). */
const STAFF_ROLES = [
  "admin",
  "hub_manager",
  "franchise_owner",
  "ngo_coordinator",
  "catalog_editor",
];

/** Make the mobile app skip the language picker it shows on a first visit. @param {import("@playwright/test").Page} page */
export async function skipWelcome(page) {
  await page.addInitScript(() => {
    if (!localStorage.getItem("gb-locale")) localStorage.setItem("gb-locale", "en-IN");
  });
}

/** One "Set-Cookie" line as Playwright's cookie. */
function cookieFrom(line, origin) {
  const [pair, ...attributes] = line.split(";").map((part) => part.trim());
  const eq = pair.indexOf("=");
  const cookie = {
    name: pair.slice(0, eq),
    value: pair.slice(eq + 1),
    httpOnly: false,
    secure: false,
    sameSite: "Lax",
    path: "/",
  };
  for (const attribute of attributes) {
    const [key, value = ""] = attribute.split("=");
    switch (key.toLowerCase()) {
      case "path":
        cookie.path = value;
        break;
      case "httponly":
        cookie.httpOnly = true;
        break;
      case "secure":
        cookie.secure = true;
        break;
      case "samesite":
        cookie.sameSite = value[0].toUpperCase() + value.slice(1).toLowerCase();
        break;
      case "max-age":
        cookie.expires = Math.floor(Date.now() / 1000) + Number(value);
        break;
    }
  }
  return { ...cookie, domain: new URL(origin).hostname };
}

/**
 * Sign in without the screens. The mobile app: the exchange goes through the app's own origin (Vite's proxy), so
 * the API's cookies land on it. The console: its server makes the call, so the exchange goes to the API and the
 * cookies are copied onto the console's origin, with the `gb_session` cookie that remembers the staff role.
 * Returns the API's GET /v1/me.
 * @param {import("@playwright/test").Page} page @param {"mobile" | "console"} app @param {string} phone
 */
export async function signInApi(page, app, phone) {
  const context = page.context();
  const origin = ORIGINS[app];
  if (app === "mobile") {
    await skipWelcome(page);
    const answer = await context.request.post(`${origin}/v1/auth/exchange`, {
      data: exchangeBody(phone),
      headers: webHeaders(origin),
    });
    if (!answer.ok())
      throw new Error(`exchange for ${phone}: ${answer.status()} ${await answer.text()}`);
    const me = await context.request.get(`${origin}/v1/me`, { headers: { "x-gb-client": "web" } });
    return me.json();
  }
  const answer = await context.request.post(`${API}/v1/auth/exchange`, {
    data: exchangeBody(phone),
    headers: webHeaders(origin),
  });
  if (!answer.ok())
    throw new Error(`exchange for ${phone}: ${answer.status()} ${await answer.text()}`);
  const lines = answer
    .headersArray()
    .filter((h) => h.name.toLowerCase() === "set-cookie")
    .map((h) => h.value);
  const cookies = lines.map((line) => cookieFrom(line, origin));
  const access = lines.find((l) => l.startsWith("__Host-gb_at="));
  const me = await (
    await context.request.get(`${API}/v1/me`, {
      headers: { "x-gb-client": "web", cookie: access.split(";")[0] },
    })
  ).json();
  const role = STAFF_ROLES.find((r) => me.roles.some((g) => g.role === r));
  if (role)
    cookies.push({ name: "gb_session", value: role, url: origin, httpOnly: true, sameSite: "Lax" });
  await context.addCookies(cookies);
  return me;
}

/**
 * The mobile app's real sign-in: the phone, "Send code", the code, "Verify". `phone` is the 10 digits.
 * @param {import("@playwright/test").Page} page @param {string} phone
 */
export async function signInMobileUi(page, phone) {
  await skipWelcome(page);
  await page.goto("/login");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByRole("button", { name: "Send code" }).click();
  await page.getByLabel("6-digit code").fill(OTP_CODE);
  await page.getByRole("button", { name: "Verify" }).click();
}
