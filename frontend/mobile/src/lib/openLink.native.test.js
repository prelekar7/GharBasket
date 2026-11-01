import { afterEach, describe, expect, it, vi } from "vitest";

const openUrl = vi.fn(async (/** @type {{ url: string }} */ _o) => ({ completed: true }));
const open = vi.fn(async (/** @type {{ url: string }} */ _o) => {});
vi.mock("@capacitor/app-launcher", () => ({ AppLauncher: { openUrl } }));
vi.mock("@capacitor/browser", () => ({ Browser: { open } }));

async function load() {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: true, MOCK: false, API_BASE: "" }));
  return import("./openLink.js");
}

afterEach(() => {
  openUrl.mockClear();
  open.mockClear();
  vi.doUnmock("@/lib/env.js");
});

describe("openLink in the Android app", () => {
  it("awaits the signed URL, then hands the PDF to the phone, with no blank tab", async () => {
    const { openLink } = await load();
    const win = vi.spyOn(window, "open");
    await openLink(async () => "https://files.example/cert.pdf?sig=1");
    expect(openUrl).toHaveBeenCalledWith({ url: "https://files.example/cert.pdf?sig=1" });
    expect(win).not.toHaveBeenCalled();
  });

  it("throws the API's error when the link cannot be had", async () => {
    const { openLink } = await load();
    await expect(
      openLink(async () => {
        throw new Error("no link");
      }),
    ).rejects.toThrow("no link");
    expect(openUrl).not.toHaveBeenCalled();
  });

  it("throws when nothing on the phone can open the link", async () => {
    const { openLink } = await load();
    openUrl.mockResolvedValueOnce({ completed: false });
    open.mockRejectedValueOnce(new Error("x"));
    await expect(openLink(async () => "https://files.example/cert.pdf")).rejects.toThrow();
  });
});
