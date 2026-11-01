import { afterEach, describe, expect, it, vi } from "vitest";

const takePhotoPlugin = vi.fn();
const chooseFromGallery = vi.fn();
vi.mock("@capacitor/camera", () => ({
  Camera: { takePhoto: takePhotoPlugin, chooseFromGallery },
  MediaTypeSelection: { Photo: 0, Video: 1, All: 2 },
}));

/** Loads camera.js with env.js reporting a native or a web build. @param {boolean} native */
async function load(native) {
  vi.resetModules();
  vi.doMock("@/lib/env.js", () => ({ NATIVE: native, MOCK: false, API_BASE: "" }));
  return import("./camera.js");
}

const cancelled = () =>
  Object.assign(new Error("Couldn't take photo because the process was canceled."), {
    code: "OS-PLUG-CAMR-0006",
  });

/** The WebView answers fetch(webPath) with the file's bytes. @param {string} [type] */
function serve(type = "image/jpeg") {
  // Not a Response: jsdom's Blob and Node's Response Blob are different classes.
  const get = vi.fn(async () => ({
    ok: true,
    status: 200,
    blob: async () => new Blob(["jpeg-bytes"], { type }),
  }));
  vi.stubGlobal("fetch", get);
  return get;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.doUnmock("@/lib/env.js");
  for (const m of [takePhotoPlugin, chooseFromGallery]) m.mockReset();
});

describe("takePhoto on the web", () => {
  it("answers null and never touches the plugin, so the screen keeps its file input", async () => {
    const { takePhoto } = await load(false);
    const get = serve();
    expect(await takePhoto()).toBeNull();
    expect(await takePhoto({ source: "gallery" })).toBeNull();
    expect(takePhotoPlugin).not.toHaveBeenCalled();
    expect(chooseFromGallery).not.toHaveBeenCalled();
    expect(get).not.toHaveBeenCalled();
  });
});

describe("takePhoto in the Android app", () => {
  it("takes a shrunk, upright JPEG by URI and reads it as a Blob", async () => {
    const { takePhoto } = await load(true);
    takePhotoPlugin.mockResolvedValue({
      type: 0,
      saved: false,
      webPath: "https://localhost/_f/1.jpg",
    });
    const get = serve();
    const photo = await takePhoto();
    expect(takePhotoPlugin).toHaveBeenCalledWith({
      quality: 70,
      targetWidth: 1600,
      targetHeight: 1600,
      correctOrientation: true,
      saveToGallery: false,
    });
    expect(get).toHaveBeenCalledWith("https://localhost/_f/1.jpg");
    expect(photo?.contentType).toBe("image/jpeg");
    expect(photo?.blob.type).toBe("image/jpeg");
    expect(photo?.blob.size).toBe(10);
    expect(photo?.name).toMatch(/^gharbasket-\d+\.jpg$/);
  });

  it("picks one photo from the gallery with the same size limits", async () => {
    const { takePhoto } = await load(true);
    chooseFromGallery.mockResolvedValue({
      results: [{ type: 0, saved: false, webPath: "https://localhost/_f/2.png" }],
    });
    serve("image/png");
    const photo = await takePhoto({ source: "gallery" });
    expect(takePhotoPlugin).not.toHaveBeenCalled();
    expect(chooseFromGallery).toHaveBeenCalledWith({
      quality: 70,
      targetWidth: 1600,
      targetHeight: 1600,
      correctOrientation: true,
      mediaType: 0,
      allowMultipleSelection: false,
    });
    expect(photo).toMatchObject({
      contentType: "image/png",
      name: expect.stringMatching(/\.png$/),
    });
  });

  it("labels a type the API does not take (e.g. HEIC) as JPEG", async () => {
    const { takePhoto } = await load(true);
    takePhotoPlugin.mockResolvedValue({ type: 0, saved: false, webPath: "https://localhost/_f/3" });
    serve("image/heic");
    expect((await takePhoto())?.contentType).toBe("image/jpeg");
  });

  it("answers null when the person backs out of the camera or the gallery, and reads nothing", async () => {
    const { takePhoto } = await load(true);
    const get = serve();
    takePhotoPlugin.mockRejectedValue(cancelled());
    expect(await takePhoto()).toBeNull();
    chooseFromGallery.mockRejectedValue(
      Object.assign(new Error("cancelled"), { code: "OS-PLUG-CAMR-0020" }),
    );
    expect(await takePhoto({ source: "gallery" })).toBeNull();
    chooseFromGallery.mockResolvedValue({ results: [] });
    expect(await takePhoto({ source: "gallery" })).toBeNull();
    expect(get).not.toHaveBeenCalled();
  });

  it("throws any other camera failure", async () => {
    const { takePhoto } = await load(true);
    takePhotoPlugin.mockRejectedValue(
      Object.assign(new Error("Couldn't access camera."), { code: "OS-PLUG-CAMR-0003" }),
    );
    await expect(takePhoto()).rejects.toThrow("Couldn't access camera.");
  });

  it("throws when the picture cannot be read", async () => {
    const { takePhoto } = await load(true);
    takePhotoPlugin.mockResolvedValue({
      type: 0,
      saved: false,
      webPath: "https://localhost/_f/4.jpg",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 404 })),
    );
    await expect(takePhoto()).rejects.toThrow("photo 404");
  });

  it("photoFile makes a File that uploads with the right name and type", async () => {
    const { photoFile } = await load(true);
    const file = photoFile({
      blob: new Blob(["x"], { type: "image/jpeg" }),
      contentType: "image/jpeg",
      name: "gharbasket-1.jpg",
    });
    expect(file).toBeInstanceOf(File);
    expect(file).toMatchObject({ name: "gharbasket-1.jpg", type: "image/jpeg", size: 1 });
  });
});
