import { describe, expect, it, vi } from "vitest";
import { openCertificate } from "@/features/partners/certificate.js";
import { exchange } from "@/lib/api.js";
import { openLink } from "./openLink.js";

/** A stand-in for the tab window.open gives back. */
const fakeTab = () => ({
  opener: /** @type {unknown} */ ("self"),
  location: { href: "" },
  close: vi.fn(),
});

describe("openLink", () => {
  it("opens the tab during the tap and fills it with the link", async () => {
    const tab = fakeTab();
    const open = vi.spyOn(window, "open").mockReturnValue(/** @type {any} */ (tab));
    await openLink(async () => "https://files.example/cert.pdf");
    expect(open).toHaveBeenCalledWith("", "_blank");
    expect(tab.location.href).toBe("https://files.example/cert.pdf");
    expect(tab.opener).toBeNull();
    expect(tab.close).not.toHaveBeenCalled();
  });

  it("closes the empty tab and rethrows when the link cannot be had", async () => {
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(/** @type {any} */ (tab));
    await expect(
      openLink(async () => {
        throw new Error("no link");
      }),
    ).rejects.toThrow("no link");
    expect(tab.close).toHaveBeenCalled();
    expect(tab.location.href).toBe("");
  });
});

// The first call loads every mock module, which is slow when the whole suite runs at once.
describe("openCertificate (the Earnings screens of the captain, kitchen and rider)", {
  timeout: 30_000,
}, () => {
  it("asks GET /v1/earnings/certificate and opens the url it answers with", async () => {
    await exchange({ devPhone: "+919800001005" });
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(/** @type {any} */ (tab));
    await openCertificate();
    expect(tab.location.href).toMatch(/^(blob:|data:application\/pdf)/);
    expect(tab.close).not.toHaveBeenCalled();
  });

  it("closes the tab and throws when the API refuses (not a partner)", async () => {
    await exchange({ devPhone: "+919800000001" });
    const tab = fakeTab();
    vi.spyOn(window, "open").mockReturnValue(/** @type {any} */ (tab));
    await expect(openCertificate()).rejects.toMatchObject({
      code: "partners.partner_not_found",
    });
    expect(tab.close).toHaveBeenCalled();
  });
});
