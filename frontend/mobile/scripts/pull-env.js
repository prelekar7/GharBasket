// `pnpm env:pull`: reads `infisical export --format=json` on stdin and prints ONLY the public
// values the app needs, renamed to VITE_* so Vite exposes them. Anything not on this list
// (database, S3, Razorpay secret…) is dropped, so a server secret can never reach the bundle.
const PUBLIC = {
  FIREBASE_API_KEY: "VITE_FIREBASE_API_KEY",
  FIREBASE_AUTH_DOMAIN: "VITE_FIREBASE_AUTH_DOMAIN",
  FIREBASE_PROJECT_ID: "VITE_FIREBASE_PROJECT_ID",
  FIREBASE_APP_ID: "VITE_FIREBASE_APP_ID",
  FIREBASE_MESSAGING_SENDER_ID: "VITE_FIREBASE_MESSAGING_SENDER_ID",
  API_BASE_URL: "VITE_API_BASE_URL",
  // "true" only in an environment that uses Firebase fictional test numbers; never forced here,
  // or a real-phone build would skip app verification.
  FIREBASE_TEST_MODE: "VITE_FIREBASE_TEST_MODE",
};

let input = "";
for await (const chunk of process.stdin) input += chunk;
const parsed = JSON.parse(input);
const secrets = Array.isArray(parsed)
  ? Object.fromEntries(parsed.map((s) => [s.key, s.value]))
  : parsed;

const lines = ["# Written by `pnpm env:pull` from Infisical staging. Public values only."];
for (const [from, to] of Object.entries(PUBLIC))
  if (secrets[from]) lines.push(`${to}=${secrets[from]}`);
console.log(lines.join("\n"));
const missing = Object.keys(PUBLIC).filter((k) => !secrets[k]);
console.error(
  `Wrote ${lines.length - 1} public values${missing.length ? `; not in Infisical yet: ${missing.join(", ")}` : ""}.`,
);
