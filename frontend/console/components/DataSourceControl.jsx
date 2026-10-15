"use client";

import { SegmentedControl } from "@gharbasket/ui";
import { parseAsStringLiteral, useQueryState } from "nuqs";
import { DATA_SOURCES } from "@/lib/data-source.js";

const values = DATA_SOURCES.map((s) => s.value);

/**
 * The "Data source" switch of every page that shows simulator or pilot numbers. The choice lives in
 * the URL (`?source=pilot`) and the server re-renders with it, so the two sources are never mixed on
 * one screen (ADR-0008).
 */
export function DataSourceControl() {
  const [source, setSource] = useQueryState(
    "source",
    parseAsStringLiteral(values).withDefault("simulator").withOptions({ shallow: false }),
  );
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs leading-4 font-semibold text-fg-secondary">Data source</span>
      <SegmentedControl
        label="Data source"
        options={DATA_SOURCES}
        value={source}
        onChange={(v) => setSource(v === "pilot" ? "pilot" : "simulator")}
      />
    </div>
  );
}
