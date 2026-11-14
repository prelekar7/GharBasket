import { describe, expect, it } from "vitest";
import { exchange, request } from "@/lib/api.js";
import { dataExportQuery, exportStage, fileSize } from "./profile.js";

/** A DataExport as GET /v1/me/data-export returns it (M1 §3.3). */
const exportOf = (/** @type {object} */ over) => ({
  id: "0199a000-0000-7000-8000-00000000e001",
  status: "queued",
  requested_at: "2026-11-02T06:00:00Z",
  ready_at: null,
  expires_at: null,
  ready_within_hours: 24,
  download: null,
  ...over,
});
const DOWNLOAD = {
  url: "https://files.example/export.json",
  expires_at: "2026-11-02T06:05:00Z",
  size_bytes: 48211,
};

describe("exportStage", () => {
  it("is none before any export, and for one that expired", () => {
    expect(exportStage(null)).toBe("none");
    expect(exportStage(/** @type {any} */ (exportOf({ status: "expired" })))).toBe("none");
  });

  it("is preparing while queued or building, and until the download link is there", () => {
    for (const status of ["queued", "building"])
      expect(exportStage(/** @type {any} */ (exportOf({ status })))).toBe("preparing");
    expect(exportStage(/** @type {any} */ (exportOf({ status: "ready" })))).toBe("preparing");
  });

  it("is ready with a download, failed when the build failed", () => {
    const ready = exportOf({
      status: "ready",
      ready_at: "2026-11-02T06:10:00Z",
      download: DOWNLOAD,
    });
    expect(exportStage(/** @type {any} */ (ready))).toBe("ready");
    expect(exportStage(/** @type {any} */ (exportOf({ status: "failed" })))).toBe("failed");
  });
});

describe("the export request", () => {
  it("reads the latest export with GET (null on identity.export_not_found); POST asks for one", async () => {
    await exchange({ devPhone: "+919800000001" });
    expect(await dataExportQuery.queryFn()).toBeNull();
    const asked = await request("/v1/me/data-export", { method: "POST" });
    expect(asked).toMatchObject({ status: "queued", download: null, ready_within_hours: 24 });
    expect(await dataExportQuery.queryFn()).toMatchObject({ id: asked.id });
  });

  it("shows file sizes in KB and MB", () => {
    expect(fileSize(48211)).toBe("47 KB");
    expect(fileSize(10)).toBe("1 KB");
    expect(fileSize(1.5 * 1024 * 1024)).toBe("1.5 MB");
  });
});
