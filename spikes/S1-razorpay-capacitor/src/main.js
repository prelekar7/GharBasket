// Spike S-1: does Razorpay Standard Checkout (checkout.js) work inside the Capacitor Android WebView,
// including UPI intent apps? Every event is logged on screen so a run can be copied into the note.
import { Capacitor } from "@capacitor/core";

const $ = (id) => document.getElementById(id);
const log = (label, data = "") => {
  const line = `${new Date().toLocaleTimeString("en-IN")} ${label} ${typeof data === "string" ? data : JSON.stringify(data)}`;
  $("log").textContent = `${line}\n${$("log").textContent}`;
  console.log(line);
};

// Android emulator reaches the host machine at 10.0.2.2; a real phone needs the laptop's LAN IP.
$("server").value =
  localStorage.getItem("s1-server") ??
  (Capacitor.getPlatform() === "android" ? "http://10.0.2.2:8787" : "http://localhost:8787");
log("platform", { platform: Capacitor.getPlatform(), ua: navigator.userAgent });

async function post(path, body) {
  const server = $("server").value.replace(/\/$/, "");
  localStorage.setItem("s1-server", server);
  const r = await fetch(server + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

/** @param {{ webview_intent?: boolean, redirect?: boolean }} variant */
async function pay(variant) {
  try {
    const order = await post("/orders", {
      amount_paise: Math.round(Number($("amount").value) * 100),
    });
    log("order", { id: order.id, amount: order.amount, status: order.status });
    if (!order.id) return;
    const rzp = new window.Razorpay({
      key: order.key_id,
      order_id: order.id,
      amount: order.amount,
      currency: "INR",
      name: "GharBasket (spike)",
      description: "Wallet top-up test",
      prefill: { contact: "+919800000002" },
      theme: { color: "#2f5d3a" },
      ...variant,
      // With redirect: true Razorpay POSTs here instead of calling handler; the spike server has no
      // such page, so this only shows whether the WebView comes back to our origin.
      ...(variant.redirect ? { callback_url: `${location.origin}/?returned=1` } : {}),
      handler: async (resp) => {
        log("handler", resp);
        log("verify", await post("/verify", resp));
      },
      modal: { ondismiss: () => log("dismissed") },
    });
    rzp.on("payment.failed", (resp) => log("payment.failed", resp.error));
    log("open", variant);
    rzp.open();
  } catch (e) {
    log("error", String(e));
  }
}

$("pay-intent").onclick = () => pay({ webview_intent: true });
$("pay-plain").onclick = () => pay({});
$("pay-redirect").onclick = () => pay({ redirect: true });
if (new URLSearchParams(location.search).has("returned")) log("returned to app via callback_url");
