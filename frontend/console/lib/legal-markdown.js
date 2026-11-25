/**
 * The public legal pages (/terms, /refund-policy, /contact, /privacy) are rendered from the reviewed
 * drafts in docs/legal/ (copied to content/legal/; a test keeps the copies identical). This reads
 * the small Markdown subset those files use, into blocks the page renders with React (no HTML
 * strings, so nothing in the text can inject markup):
 *
 * `#`/`##`/`###` headings, paragraphs, `-` lists and `1.` lists (nested by indentation), `|` tables,
 * `**bold**`, `[text](link)` and HTML comments (dropped). A `[CAPITALISED PLACEHOLDER]` that is not a
 * link is kept as its own inline, so the page can mark it and show the draft banner while any remain.
 */

/**
 * @typedef {{ type: "text", text: string } | { type: "strong", children: Inline[] } | { type: "link", href: string, children: Inline[] } | { type: "placeholder", text: string }} Inline
 * @typedef {{ text: Inline[], children: ListBlock[] }} ListItem
 * @typedef {{ type: "list", ordered: boolean, items: ListItem[] }} ListBlock
 * @typedef {{ type: "heading", level: 1 | 2 | 3, id: string, text: Inline[] } | { type: "paragraph", text: Inline[] } | ListBlock | { type: "table", head: Inline[][], rows: Inline[][][] }} Block
 */

/** The other legal documents, by their file name in docs/legal, and where they live on the site. */
const DOC_LINKS = /** @type {Record<string, string>} */ ({
  "terms.md": "/terms",
  "refunds.md": "/refund-policy", // /refunds is the console's admin page
  "contact.md": "/contact",
  "privacy.md": "/privacy",
});

/** `[LEGAL BUSINESS NAME]`, `[EFFECTIVE DATE]`, `[GSTIN if registered]`: two capitals to start. */
const PLACEHOLDER = /^\[[A-Z]{2}[^\]]*\]/;

/**
 * Inline Markdown: bold, links, placeholders and text.
 * @param {string} source
 * @returns {Inline[]}
 */
export function parseInline(source) {
  /** @type {Inline[]} */
  const out = [];
  let text = "";
  const flush = () => {
    if (text) out.push({ type: "text", text });
    text = "";
  };
  let i = 0;
  while (i < source.length) {
    const rest = source.slice(i);
    if (rest.startsWith("**")) {
      const end = rest.indexOf("**", 2);
      if (end > 2) {
        flush();
        out.push({ type: "strong", children: parseInline(rest.slice(2, end)) });
        i += end + 2;
        continue;
      }
    }
    if (rest.startsWith("[")) {
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)/.exec(rest);
      if (link) {
        flush();
        out.push({ type: "link", href: hrefOf(link[2]), children: parseInline(link[1]) });
        i += link[0].length;
        continue;
      }
      const placeholder = PLACEHOLDER.exec(rest);
      if (placeholder) {
        flush();
        out.push({ type: "placeholder", text: placeholder[0] });
        i += placeholder[0].length;
        continue;
      }
    }
    text += source[i];
    i += 1;
  }
  flush();
  return out;
}

/**
 * Where a link goes on the site: another legal document's page (with its #anchor), a web or mail
 * address as written, anything else (a repository path) to nowhere useful, so it becomes text.
 * @param {string} target
 */
function hrefOf(target) {
  const [file, anchor] = target.split("#");
  if (DOC_LINKS[file]) return DOC_LINKS[file] + (anchor ? `#${anchor}` : "");
  if (target.startsWith("#") || /^(https:|mailto:|tel:)/.test(target)) return target;
  return "";
}

/** The anchor of a heading: "4. The weekly cycle" becomes "the-weekly-cycle". @param {string} text */
export function slugOf(text) {
  return text
    .replace(/\*\*|\[|\]/g, "")
    .toLowerCase()
    .replace(/^\d+\.\s*/, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

const LIST_ITEM = /^(\s*)([-*]|\d+\.)\s+(.*)$/;

/** @param {string} line */
const cellsOf = (line) =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => parseInline(cell.trim()));

/**
 * Block Markdown: the whole file.
 * @param {string} source
 * @returns {Block[]}
 */
export function parseMarkdown(source) {
  const lines = source.replace(/<!--[\s\S]*?-->/g, "").split("\n");
  /** @type {Block[]} */
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      const level = /** @type {1 | 2 | 3} */ (heading[1].length);
      blocks.push({
        type: "heading",
        level,
        id: slugOf(heading[2]),
        text: parseInline(heading[2]),
      });
      i += 1;
      continue;
    }
    if (line.trimStart().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trimStart().startsWith("|")) rows.push(lines[i++]);
      const body = rows.slice(2).map(cellsOf); // row 2 is the |---| line
      blocks.push({ type: "table", head: cellsOf(rows[0]), rows: body });
      continue;
    }
    if (LIST_ITEM.test(line)) {
      const items = [];
      while (i < lines.length && (LIST_ITEM.test(lines[i]) || /^\s{2,}\S/.test(lines[i])))
        items.push(lines[i++]);
      blocks.push(parseList(items, 0));
      continue;
    }
    const paragraph = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^#{1,3}\s/.test(lines[i]) &&
      !LIST_ITEM.test(lines[i]) &&
      !lines[i].trimStart().startsWith("|")
    )
      paragraph.push(lines[i++].trim());
    blocks.push({ type: "paragraph", text: parseInline(paragraph.join(" ")) });
  }
  return blocks;
}

/**
 * One list and the lists nested in it (deeper indentation belongs to the item above).
 * @param {string[]} lines
 * @param {number} indent
 * @returns {ListBlock}
 */
function parseList(lines, indent) {
  const first = LIST_ITEM.exec(lines[0]);
  /** @type {ListBlock} */
  const list = { type: "list", ordered: /\d/.test(first?.[2] ?? ""), items: [] };
  let i = 0;
  while (i < lines.length) {
    const match = LIST_ITEM.exec(lines[i]);
    if (!match || match[1].length > indent) {
      i += 1; // a stray deeper line without an item above: skipped
      continue;
    }
    /** @type {ListItem} */
    const item = { text: parseInline(match[3]), children: [] };
    i += 1;
    const nested = [];
    while (i < lines.length) {
      const deeper = LIST_ITEM.exec(lines[i]);
      if (deeper && deeper[1].length <= indent) break;
      nested.push(lines[i++]);
    }
    if (nested.length) {
      const depth = LIST_ITEM.exec(nested[0])?.[1].length ?? indent + 2;
      item.children.push(parseList(nested, depth));
    }
    list.items.push(item);
  }
  return list;
}

/**
 * Whether any `[PLACEHOLDER]` is left: the page shows the draft banner until there are none.
 * @param {Block[]} blocks
 */
export function hasPlaceholders(blocks) {
  /** @param {Inline[]} inlines @returns {boolean} */
  const inInlines = (inlines) =>
    inlines.some(
      (n) =>
        n.type === "placeholder" ||
        ((n.type === "strong" || n.type === "link") && inInlines(n.children)),
    );
  /** @param {ListBlock} list @returns {boolean} */
  const inList = (list) =>
    list.items.some((item) => inInlines(item.text) || item.children.some(inList));
  return blocks.some((b) => {
    if (b.type === "list") return inList(b);
    if (b.type === "table") return [b.head, ...b.rows].some((row) => row.some(inInlines));
    return inInlines(b.text);
  });
}

/**
 * Plain text of inlines (for the page title and the table of contents).
 * @param {Inline[]} inlines
 * @returns {string}
 */
export function plainText(inlines) {
  return inlines
    .map((n) => (n.type === "text" || n.type === "placeholder" ? n.text : plainText(n.children)))
    .join("");
}
