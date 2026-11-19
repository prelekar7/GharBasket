import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import {
  hasPlaceholders,
  parseInline,
  parseMarkdown,
  plainText,
  slugOf,
} from "./legal-markdown.js";

const CONTENT = join(import.meta.dirname, "..", "content", "legal");
const DOCS = join(import.meta.dirname, "..", "..", "..", "docs", "legal");
const FILES = ["terms.md", "refunds.md", "contact.md", "privacy.md"];

test("the console's copies are the reviewed drafts in docs/legal, unchanged", () => {
  for (const file of FILES)
    assert.equal(
      readFileSync(join(CONTENT, file), "utf8"),
      readFileSync(join(DOCS, file), "utf8"),
      `content/legal/${file} differs from docs/legal/${file}: copy it again (cp docs/legal/${file} frontend/console/content/legal/)`,
    );
});

test("inline: bold, links to the other documents, placeholders and plain text", () => {
  assert.deepEqual(
    parseInline("by **[LEGAL BUSINESS NAME]** and our [Privacy Notice](privacy.md)."),
    [
      { type: "text", text: "by " },
      { type: "strong", children: [{ type: "placeholder", text: "[LEGAL BUSINESS NAME]" }] },
      { type: "text", text: " and our " },
      { type: "link", href: "/privacy", children: [{ type: "text", text: "Privacy Notice" }] },
      { type: "text", text: "." },
    ],
  );
  assert.deepEqual(parseInline("[x](refunds.md#7-where)")[0], {
    type: "link",
    href: "/refund-policy#7-where",
    children: [{ type: "text", text: "x" }],
  });
  // A repository path is not a page on the site.
  assert.equal(/** @type {any} */ (parseInline("[README](README.md)")[0]).href, "");
  assert.deepEqual(parseInline("[a] b"), [{ type: "text", text: "[a] b" }]);
});

test("blocks: headings with anchors, nested lists, tables, comments dropped", () => {
  const blocks = parseMarkdown(
    [
      "<!-- DRAFT -->",
      "# Terms",
      "",
      "Para one",
      "continues.",
      "",
      "## 4. If you are not home",
      "1. **Same day:** kept.",
      "2. Fails:",
      "   - Fresh items refunded.",
      "   - Others kept.",
      "",
      "| You want | Go to |",
      "|---|---|",
      "| Refund | **Wallet** |",
    ].join("\n"),
  );
  assert.deepEqual(
    blocks.map((b) => b.type),
    ["heading", "paragraph", "heading", "list", "table"],
  );
  assert.equal(/** @type {any} */ (blocks[2]).id, "if-you-are-not-home");
  assert.equal(plainText(/** @type {any} */ (blocks[1]).text), "Para one continues.");
  const list = /** @type {any} */ (blocks[3]);
  assert.equal(list.ordered, true);
  assert.equal(list.items.length, 2);
  assert.equal(list.items[1].children[0].items.length, 2);
  const table = /** @type {any} */ (blocks[4]);
  assert.equal(table.rows.length, 1);
  assert.equal(plainText(table.rows[0][1]), "Wallet");
});

test("placeholders are found anywhere, and every draft has some today", () => {
  assert.equal(hasPlaceholders(parseMarkdown("All filled in.")), false);
  assert.equal(hasPlaceholders(parseMarkdown("| a |\n|---|\n| **[SUPPORT EMAIL]** |")), true);
  for (const file of FILES)
    assert.equal(hasPlaceholders(parseMarkdown(readFileSync(join(CONTENT, file), "utf8"))), true);
});

test("every legal file parses into a title and sections", () => {
  for (const file of FILES) {
    const blocks = parseMarkdown(readFileSync(join(CONTENT, file), "utf8"));
    assert.equal(blocks[0].type, "heading", file);
    assert.ok(blocks.filter((b) => b.type === "heading" && b.level === 2).length >= 3, file);
  }
  assert.equal(slugOf("10. Test phase"), "test-phase");
});
