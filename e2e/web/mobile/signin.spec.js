// Signing in to the mobile app on the real backend: the screens with a fresh phone, then each seed role's shell.
import { expect, expectNoErrorPage, PEOPLE, signInApi, stack, test } from "../fixtures/index.js";
import { signInMobileUi } from "../fixtures/signin.js";

test.describe.configure({ mode: "serial" });
test.beforeAll(() => stack.reset());

test("a fresh phone signs in with the code 123456 and starts onboarding", async ({ page }) => {
  await signInMobileUi(page, "9800001020");
  // No household yet: the customer shell sends a new person to set one up.
  await expect(page).toHaveURL(/\/onboarding\//);
  await expectNoErrorPage(page, "mobile", { tabs: false });
});

test("a customer with a household lands on the week", async ({ page }) => {
  const me = await signInApi(page, "mobile", PEOPLE.priya);
  expect(me.roles.map((r) => r.role)).toEqual(["customer"]);
  await page.goto("/");
  await expect(page).toHaveURL(/\/week/);
  await expectNoErrorPage(page, "mobile");
});

// Each of these holds a field role beside customer, so the app asks which one to continue as.
const SHELLS = [
  ["captain", "Society Captain", /\/captain/],
  ["kitchen", "Kitchen partner", /\/kitchen/],
  ["rider", "Rider", /\/rider/],
  ["packer", "Packer", /\/packer/],
];

for (const [person, button, home] of SHELLS) {
  test(`${person} chooses the ${button} role and lands in that shell`, async ({ page }) => {
    await signInApi(page, "mobile", PEOPLE[person]);
    await page.goto("/");
    await expect(page).toHaveURL(/\/role/);
    await expect(page.getByRole("button", { name: "Customer" })).toBeVisible();
    await page.getByRole("button", { name: button }).click();
    await expect(page).toHaveURL(home);
    // The shell's own endpoints may not exist yet (404 http.not_found): the page still renders.
    await expectNoErrorPage(page, "mobile");
  });
}
