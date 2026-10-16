// Reads `infisical export --format=json` on stdin and prints ONLY the public Firebase web values,
// renamed to VITE_* so Vite exposes them. Anything not on this list (database, S3, Razorpay
// secret…) is dropped, so a server secret can never reach the browser bundle.
const PUBLIC = {
  FIREBASE_API_KEY: "VITE_FIREBASE_API_KEY",
  FIREBASE_AUTH_DOMAIN: "VITE_FIREBASE_AUTH_DOMAIN",
  FIREBASE_PROJECT_ID: "VITE_FIREBASE_PROJECT_ID",
  FIREBASE_APP_ID: "VITE_FIREBASE_APP_ID",
  FIREBASE_MESSAGING_SENDER_ID: "VITE_FIREBASE_MESSAGING_SENDER_ID",
};

let input = "";
for await (const chunk of process.stdin) input += chunk;
const parsed = JSON.parse(input);
const secrets = Array.isArray(parsed)
  ? Object.fromEntries(parsed.map((s) => [s.key, s.value]))
  : parsed;

const missing = Object.keys(PUBLIC).filter((k) => !secrets[k]);
if (missing.length) {
  console.error(`Missing in Infisical staging: ${missing.join(", ")}`);
  process.exit(1);
}
for (const [from, to] of Object.entries(PUBLIC)) console.log(`${to}=${secrets[from]}`);
console.error(`Wrote ${Object.keys(PUBLIC).length} public Firebase values (no secrets).`);
