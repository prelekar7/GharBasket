import assert from "node:assert/strict";
import { test } from "node:test";
import { amountRefusal, resolveBody, rupeesText } from "./order-issues.js";

test("rupeesText writes paise as an amount the field can read back", () => {
  assert.equal(rupeesText(4000), "40");
  assert.equal(rupeesText(4050), "40.50");
  assert.equal(rupeesText(5), "0.05");
});

test("a credit needs an amount between 1 paisa and the maximum, in integer paise", () => {
  const ok = resolveBody({ outcome: "credit", rupees: "40.50", note: " ", maxPaise: 5000 });
  assert.deepEqual(ok, { body: { outcome: "credit", credit_paise: 4050 } });
  for (const rupees of ["", "0", "abc", "1.234"])
    assert.equal(
      "error" in resolveBody({ outcome: "credit", rupees, note: "", maxPaise: 5000 }),
      true,
    );
  const over = resolveBody({ outcome: "credit", rupees: "51", note: "", maxPaise: 5000 });
  assert.ok("error" in over && over.error.includes("₹50"));
});

test("a reject needs a note and sends no amount", () => {
  assert.equal(
    "error" in resolveBody({ outcome: "reject", rupees: "10", note: " ", maxPaise: 0 }),
    true,
  );
  assert.deepEqual(
    resolveBody({ outcome: "reject", rupees: "10", note: "Not our bag", maxPaise: 0 }),
    {
      body: { outcome: "reject", note: "Not our bag" },
    },
  );
});

test("the refusals that carry an amount say the amount", () => {
  assert.match(amountRefusal("orders.issue_credit_invalid", { max_paise: 4000 }) ?? "", /₹40/);
  assert.match(amountRefusal("billing.wallet_limit", { room_paise: 1500 }) ?? "", /₹15/);
  assert.equal(amountRefusal("orders.issue_not_open", {}), null);
});
