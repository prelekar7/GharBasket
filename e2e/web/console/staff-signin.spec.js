// Signing in to the console on the real backend: each staff seed person reaches their home (or its "not available yet"),
// and a customer's number ends on /not-staff.
import { expect, expectNoErrorPage, PEOPLE, signInApi, stack, test } from "../fixtures/index.js";

test.describe.configure({ mode: "serial" });
test.beforeAll(() => stack.reset());

test("a customer's number ends on /not-staff, through the real sign-in screens", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("Mobile number").fill("9800001001");
  await page.getByRole("button", { name: "Send code" }).click();
  await page.getByLabel("6-digit code").fill("123456");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).toHaveURL(/\/not-staff/);
  await expect(
    page.getByRole("heading", { name: "This console is for GharBasket staff" }),
  ).toBeVisible();
});

// The console's pages need the backend's M12 analytics, M9 NGO and other endpoints, most of them not built (docs/integration/
// contract-gaps.md). A page that calls one without orNotShipped( ends on an error page; those tests are marked test.fail
// with the fix, so the suite turns red the day the frontend fix or the endpoint lands and the mark must go.
const HOMES = [
  ["admin", "Neha Iyer", /\/catalog/, null],
  ["hubManager", "Vikram Desai", /\/today/, null],
  ["franchiseOwner", "Sanjay Mehta", /\/pnl/, null],
  ["ngo", "Asha Fernandes", /\/handoffs/, null],
];

for (const [person, name, home, broken] of HOMES) {
  test(`${name} (${person}) reaches the console home`, async ({ page }) => {
    if (broken) test.fail(true, broken);
    await signInApi(page, "console", PEOPLE[person]);
    await page.goto("/");
    await expect(page).toHaveURL(home);
    await expectNoErrorPage(page, "console");
  });
}

test("Rekha Joshi (catalog editor) reaches the console", async ({ page }) => {
  await signInApi(page, "console", PEOPLE.catalogEditor);
  await page.goto("/");
  await expect(page).toHaveURL(/\/catalog/);
  await expectNoErrorPage(page, "console");
});
