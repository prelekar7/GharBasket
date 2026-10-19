import { useEffect, useId, useRef, useState } from "react";
import { formatPaise } from "../components/Money.jsx";
import { readToken, useThemeVersion } from "./mapShared.jsx";

/** @typedef {{ name: string, values: number[] }} Series */

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
/** Different marker shapes per series, so a line is never told apart by colour alone. */
const SYMBOLS = ["circle", "rect", "triangle"];

/** @type {Promise<any> | undefined} */
let pending;
/** Loads only the ECharts parts used here (line, bar, grid, tooltip, aria, SVG renderer), on first use. */
function loadECharts() {
  pending ??= (async () => {
    const [core, charts, components, renderers] = await Promise.all([
      import("echarts/core"),
      import("echarts/charts"),
      import("echarts/components"),
      import("echarts/renderers"),
    ]);
    core.use([
      charts.LineChart,
      charts.BarChart,
      components.GridComponent,
      components.TooltipComponent,
      components.AriaComponent,
      renderers.SVGRenderer,
    ]);
    return core;
  })();
  return pending;
}

/**
 * @param {"pct" | "money" | undefined} format @param {string | undefined} unit
 * @returns {(v: number | undefined) => string}
 */
const formatter = (format, unit) => (v) => {
  if (v == null) return "";
  if (format === "money") return formatPaise(v);
  if (format === "pct") return `${v}%`;
  return inr.format(v) + (unit ? ` ${unit}` : "");
};

/**
 * Line or bar chart on Apache ECharts, coloured from the design tokens (so it follows light and dark).
 * ECharts loads on first render; the data is also in a table for screen readers. `format: "money"` means
 * the values are integer paise; "pct" shows a % sign; otherwise numbers get Indian grouping and `unit`.
 * @param {{ title: string, subtitle?: string, kind?: "bar" | "line", labels: string[], series: Series[],
 *   format?: "pct" | "money", unit?: string, max?: number, note?: string, height?: number }} props
 */
export function ChartCard({
  title,
  subtitle,
  kind = "bar",
  labels,
  series,
  format,
  unit,
  max,
  note,
  height = 220,
}) {
  const id = useId();
  const box = useRef(/** @type {HTMLDivElement | null} */ (null));
  const [chart, setChart] = useState(/** @type {any} */ (null));
  const theme = useThemeVersion();
  const fmt = formatter(format, unit);
  const colours = ["--gb-primary", "--gb-info", "--gb-accent-text"];

  // Create the chart once ECharts has loaded; keep it the size of its box.
  useEffect(() => {
    let cancelled = false;
    /** @type {any} */
    let instance;
    /** @type {ResizeObserver | undefined} */
    let observer;
    loadECharts()
      .then((echarts) => {
        if (cancelled || !box.current) return;
        instance = echarts.init(box.current, null, { renderer: "svg" });
        observer = new ResizeObserver(() => instance?.resize());
        observer.observe(box.current);
        setChart(instance);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      observer?.disconnect();
      instance?.dispose();
      setChart(null);
    };
  }, []);

  // Draw (and redraw on data or theme change) with colours read from the tokens.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `theme` re-reads the token colours when light/dark changes
  useEffect(() => {
    if (!chart || !box.current) return;
    const el = box.current;
    const c = {
      text: readToken(el, "--gb-text", "#1e1b16"),
      secondary: readToken(el, "--gb-text-secondary", "#4a4339"),
      line: readToken(el, "--gb-line", "#e2d6c2"),
      lineStrong: readToken(el, "--gb-line-strong", "#8f836f"),
      surface: readToken(el, "--gb-surface", "#f3ebdd"),
    };
    const palette = colours.map((n) => readToken(el, n, "#2f5d3a"));
    const font = getComputedStyle(el).fontFamily;
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    chart.setOption(
      {
        animation: !calm,
        color: palette,
        textStyle: { fontFamily: font, color: c.secondary },
        aria: { enabled: true, label: { enabled: false }, decal: { show: series.length > 1 } },
        grid: { left: 4, right: 12, top: 12, bottom: 4, containLabel: true },
        tooltip: {
          trigger: "axis",
          confine: true,
          backgroundColor: c.surface,
          borderColor: c.line,
          textStyle: { color: c.text, fontFamily: font },
          valueFormatter: fmt,
        },
        xAxis: {
          type: "category",
          data: labels,
          axisTick: { show: false },
          axisLine: { lineStyle: { color: c.lineStrong } },
          axisLabel: { color: c.secondary, fontFamily: font },
        },
        yAxis: {
          type: "value",
          min: 0,
          max,
          splitLine: { lineStyle: { color: c.line } },
          axisLabel: {
            color: c.secondary,
            fontFamily: font,
            formatter: (/** @type {number} */ v) => fmt(v),
          },
        },
        series: series.map((s, i) => ({
          name: s.name,
          type: kind,
          data: s.values,
          ...(kind === "line"
            ? {
                symbol: SYMBOLS[i % 3],
                symbolSize: 9,
                lineStyle: { width: 2.5 },
                itemStyle: { borderColor: c.surface, borderWidth: 1.5 },
              }
            : { barMaxWidth: 36, itemStyle: { borderRadius: [2, 2, 0, 0] } }),
        })),
      },
      { notMerge: true },
    );
  }, [chart, kind, labels, series, format, unit, max, theme]);

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex min-w-0 flex-col gap-2 rounded-md bg-surface p-4 text-fg"
    >
      <div>
        <h3 id={`${id}-title`} className="m-0 text-base leading-[22px] font-bold">
          {title}
        </h3>
        {subtitle && <p className="m-0 text-[13px] leading-[18px] text-fg-secondary">{subtitle}</p>}
      </div>
      {/* The picture is not read aloud; the table below holds the same numbers. */}
      <div aria-hidden ref={box} style={{ height }} className="w-full" />
      {series.length > 1 && (
        <ul className="m-0 flex list-none flex-wrap gap-4 p-0 text-[13px] text-fg-secondary">
          {series.map((s, i) => (
            <li key={s.name} className="inline-flex items-center gap-2">
              <span
                aria-hidden
                className="size-3 rounded-xs"
                style={{ background: `var(${colours[i % 3]})` }}
              />
              {s.name}
            </li>
          ))}
        </ul>
      )}
      <table className="sr-only">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">{" "}</th>
            {series.map((s) => (
              <th key={s.name} scope="col">
                {s.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {labels.map((l, i) => (
            <tr key={l}>
              <th scope="row">{l}</th>
              {series.map((s) => (
                <td key={s.name}>{fmt(s.values[i])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {note && <p className="m-0 text-xs leading-4 text-fg-secondary">{note}</p>}
    </section>
  );
}
