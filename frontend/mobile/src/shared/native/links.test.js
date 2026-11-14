import { afterEach, describe, expect, it, vi } from "vitest";

const openUrl = vi.fn(async (/** @type {{ url: string }} */ _o) => ({ completed: true }));
const open = vi.fn(async (/** @type {{ url: string }} */ _o) => {});
vi.mock("@capacitor/app-launcher", () => ({ AppLauncher: { openUrl } }));
vi.mock("@capacitor/browser", () => ({ Browser: { open } }));

/** @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./links.js");
}

afterEach(() => {
  openUrl.mockClear();
  open.mockClear();
  vi.doUnmock("@/lib/env.js");
});

describe("wantsApp", () => {
  it.each([
    "https://wa.me/919800000001",
    "upi://pay?pa=a@b",
    "tel:+919800000001",
    "geo:19.07,72.87",
    "google.navigation:q=19.07,72.87",
    "https://www.google.com/maps/dir/?api=1&destination=19.07,72.87",
    "https://maps.google.com/?q=x",
    "https://files.example/cert.PDF?sig=1",
  ])("hands %s to an installed app", async (url) => {
    const { wantsApp } = await load(true);
    expect(wantsApp(url)).toBe(true);
  });

  it.each([
    "https://gharbasket.co.in/s/green-park",
    "https://example.com/google.com/maps",
    "nonsense",
  ])("leaves %s to the browser", async (url) => {
    const { wantsApp } = await load(true);
    expect(wantsApp(url)).toBe(false);
  });
});

describe("openExternal", () => {
  it("opens a new tab on the web", async () => {
    const { openExternal } = await load(false);
    const tab = vi.spyOn(window, "open").mockReturnValue(null);
    expect(await openExternal("https://wa.me/1")).toBe(true);
    expect(tab).toHaveBeenCalledWith("https://wa.me/1", "_blank", "noopener");
    expect(openUrl).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
  });

  it("opens WhatsApp, UPI, phone and Maps links in their apps", async () => {
    const { openExternal } = await load(true);
    expect(await openExternal("https://wa.me/1")).toBe(true);
    expect(await openExternal("tel:+911")).toBe(true);
    expect(openUrl.mock.calls.map(([o]) => o.url)).toEqual(["https://wa.me/1", "tel:+911"]);
    expect(open).not.toHaveBeenCalled();
  });

  it("opens other web pages in the in-app browser", async () => {
    const { openExternal } = await load(true);
    expect(await openExternal("https://gharbasket.co.in/help")).toBe(true);
    expect(open).toHaveBeenCalledWith({ url: "https://gharbasket.co.in/help" });
    expect(openUrl).not.toHaveBeenCalled();
  });

  it("falls back to the browser when no app takes a web link", async () => {
    const { openExternal } = await load(true);
    openUrl.mockRejectedValueOnce(new Error("no app"));
    expect(await openExternal("https://wa.me/1")).toBe(true);
    expect(open).toHaveBeenCalledWith({ url: "https://wa.me/1" });
  });

  it("answers false, never throws, when nothing can open it", async () => {
    const { openExternal } = await load(true);
    openUrl.mockResolvedValueOnce({ completed: false });
    expect(await openExternal("upi://pay?pa=a@b")).toBe(false);
    open.mockRejectedValueOnce(new Error("x"));
    expect(await openExternal("https://gharbasket.co.in")).toBe(false);
  });
});
