import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import * as Icons from "./Icons.jsx";
import * as Illustrations from "./Illustrations.jsx";

const html = (/** @type {any} */ C, props = {}) => renderToStaticMarkup(createElement(C, props));

describe("custom art", () => {
  it("has all 15 icons, drawn in the text colour and hidden from screen readers by default", () => {
    expect(Object.keys(Icons)).toHaveLength(15);
    for (const Icon of Object.values(Icons)) {
      const svg = html(Icon);
      expect(svg).toContain('stroke="currentColor"');
      expect(svg).toContain('aria-hidden="true"');
    }
  });

  it("names an icon or illustration when a label is given", () => {
    expect(html(Icons.GullakIcon, { label: "Kindness" })).toContain('aria-label="Kindness"');
    expect(html(Illustrations.EmptyBasket, { label: "Empty basket" })).toContain('role="img"');
  });

  it("uses only theme colours in the illustrations", () => {
    for (const Art of Object.values(Illustrations)) expect(html(Art)).not.toMatch(/#[0-9a-f]{6}/i);
  });
});
