import { afterEach, describe, expect, it, vi } from "vitest";

const share = vi.fn(async (/** @type {object} */ _o) => ({}));
vi.mock("@capacitor/share", () => ({ Share: { share } }));

/** @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./share.js");
}

const content = { title: "GharBasket", text: "Join us", url: "https://gharbasket.co.in/s/x" };

afterEach(() => {
  share.mockClear();
  vi.doUnmock("@/lib/env.js");
  Object.assign(navigator, { share: undefined, clipboard: undefined });
});

describe("shareContent", () => {
  it("opens the Android share sheet in the app", async () => {
    const { shareContent } = await load(true);
    expect(await shareContent(content)).toBe("shared");
    expect(share).toHaveBeenCalledWith({ ...content, dialogTitle: "GharBasket" });
  });

  it("treats closing the Android sheet as dismissed, other errors as failed", async () => {
    const { shareContent } = await load(true);
    share.mockRejectedValueOnce(new Error("Share canceled"));
    expect(await shareContent(content)).toBe("dismissed");
    share.mockRejectedValueOnce(new Error("boom"));
    expect(await shareContent(content)).toBe("failed");
  });

  it("uses Web Share on the web", async () => {
    const { shareContent } = await load(false);
    const webShare = vi.fn(async () => {});
    Object.assign(navigator, { share: webShare });
    expect(await shareContent(content)).toBe("shared");
    expect(webShare).toHaveBeenCalledWith(content);
    expect(share).not.toHaveBeenCalled();
  });

  it("copies to the clipboard when the web cannot share", async () => {
    const { shareContent } = await load(false);
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    expect(await shareContent(content)).toBe("copied");
    expect(writeText).toHaveBeenCalledWith("Join us https://gharbasket.co.in/s/x");
  });

  it("treats a closed Web Share sheet as dismissed", async () => {
    const { shareContent } = await load(false);
    Object.assign(navigator, {
      share: vi.fn(async () => {
        throw new DOMException("closed", "AbortError");
      }),
    });
    expect(await shareContent(content)).toBe("dismissed");
  });
});
