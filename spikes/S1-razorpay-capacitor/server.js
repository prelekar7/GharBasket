// Throwaway order + signature server for spike S-1. Stands in for the M4 billing endpoints.
import { createHmac, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";

const { RAZORPAY_KEY_ID: keyId, RAZORPAY_KEY_SECRET: secret, PORT = "8787" } = process.env;
if (!keyId?.startsWith("rzp_test_") || !secret) throw new Error("Set Razorpay TEST keys in .env");

const json = (res, status, body) => {
  res.writeHead(status, {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "content-type",
  });
  res.end(JSON.stringify(body));
};

createServer(async (req, res) => {
  if (req.method === "OPTIONS") return json(res, 204, {});
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {};

  if (req.method === "POST" && req.url === "/orders") {
    // W3 top-up: amount in paise.
    const r = await fetch("https://api.razorpay.com/v1/orders", {
      method: "POST",
      headers: {
        authorization: `Basic ${Buffer.from(`${keyId}:${secret}`).toString("base64")}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        amount: body.amount_paise ?? 50000,
        currency: "INR",
        receipt: `s1-${Date.now()}`,
      }),
    });
    const order = await r.json();
    return json(res, r.status, { ...order, key_id: keyId });
  }

  if (req.method === "POST" && req.url === "/verify") {
    const expected = createHmac("sha256", secret)
      .update(`${body.razorpay_order_id}|${body.razorpay_payment_id}`)
      .digest("hex");
    const given = Buffer.from(String(body.razorpay_signature ?? ""));
    const ok = given.length === expected.length && timingSafeEqual(given, Buffer.from(expected));
    return json(res, 200, { signature_valid: ok });
  }

  json(res, 404, { error: "not found" });
}).listen(Number(PORT), "0.0.0.0", () => console.log(`S-1 server on :${PORT}`));
