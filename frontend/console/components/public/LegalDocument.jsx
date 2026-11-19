import { readFileSync } from "node:fs";
import { join } from "node:path";
import Link from "next/link";
import { Fragment } from "react";
import { PublicFooter, PublicHeader } from "@/components/public/Chrome.jsx";
import { hasPlaceholders, parseMarkdown, plainText } from "@/lib/legal-markdown.js";

/** @typedef {"terms" | "refunds" | "contact" | "privacy"} LegalDoc */

/**
 * One legal document from content/legal/ (a copy of docs/legal/, kept identical by
 * lib/legal-markdown.test.js), parsed once at build time: the pages are static.
 * @param {LegalDoc} doc
 */
export function loadLegal(doc) {
  const blocks = parseMarkdown(
    readFileSync(join(process.cwd(), "content", "legal", `${doc}.md`), "utf8"),
  );
  const title = blocks[0]?.type === "heading" ? plainText(blocks[0].text) : doc;
  return { blocks, title, draft: hasPlaceholders(blocks) };
}

/** @param {{ inlines: import("@/lib/legal-markdown.js").Inline[] }} props */
function Inlines({ inlines }) {
  return inlines.map((node, i) => {
    const key = `${i}`;
    switch (node.type) {
      case "strong":
        return (
          <strong key={key}>
            <Inlines inlines={node.children} />
          </strong>
        );
      case "link":
        return node.href ? (
          <Link key={key} href={node.href} className="text-link">
            <Inlines inlines={node.children} />
          </Link>
        ) : (
          <Fragment key={key}>
            <Inlines inlines={node.children} />
          </Fragment>
        );
      case "placeholder":
        // Shown as written, marked, so a reader of the draft sees what is still to come.
        return (
          <mark key={key} className="rounded-sm bg-accent-soft px-1 text-fg">
            {node.text}
          </mark>
        );
      default:
        return <Fragment key={key}>{node.text}</Fragment>;
    }
  });
}

/** @param {{ list: import("@/lib/legal-markdown.js").ListBlock }} props */
function List({ list }) {
  const Tag = list.ordered ? "ol" : "ul";
  return (
    <Tag className={`flex flex-col gap-1.5 pl-5 ${list.ordered ? "list-decimal" : "list-disc"}`}>
      {list.items.map((item, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: a static document parsed at build time; it never reorders
        <li key={`${i}`}>
          <Inlines inlines={item.text} />
          {item.children.map((child, j) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: a static document parsed at build time; it never reorders
            <div key={`${j}`} className="mt-1.5">
              <List list={child} />
            </div>
          ))}
        </li>
      ))}
    </Tag>
  );
}

/**
 * A legal page in the layout of the privacy notice (Privacy.dc.html): "On this page" from the
 * document's sections, then the text. While a `[PLACEHOLDER]` remains the page says it is a draft;
 * the banner goes by itself once docs/legal is filled in and copied here.
 * @param {{ doc: LegalDoc }} props
 */
export function LegalDocument({ doc }) {
  const { blocks, draft } = loadLegal(doc);
  const sections = blocks.filter((b) => b.type === "heading" && b.level === 2);
  return (
    <>
      <PublicHeader wide />
      <div className="grid flex-1 gap-8 px-4 py-8 min-[900px]:grid-cols-[240px_minmax(0,720px)] min-[900px]:gap-16 min-[900px]:px-16 min-[900px]:py-12">
        <nav
          aria-label="On this page"
          className="min-[900px]:sticky min-[900px]:top-6 min-[900px]:max-h-[calc(100dvh-3rem)] min-[900px]:self-start min-[900px]:overflow-y-auto"
        >
          <p className="mb-1.5 text-[15px] leading-[22px] font-bold text-fg-secondary">
            On this page
          </p>
          <ul className="flex flex-col">
            {sections.map((s) =>
              s.type === "heading" ? (
                <li key={s.id}>
                  <a href={`#${s.id}`} className="block py-1.5 text-[15px] text-fg no-underline">
                    {plainText(s.text)}
                  </a>
                </li>
              ) : null,
            )}
          </ul>
        </nav>
        <main id="content" className="flex flex-col gap-4 text-[17px] leading-7">
          {draft && (
            <p
              role="note"
              className="rounded-sm bg-accent-soft px-4 py-3 text-[15px] leading-[22px]"
            >
              <strong>Draft.</strong> This page is being finalised before GharBasket opens to
              everyone. The marked parts are still to be filled in.
            </p>
          )}
          {blocks.map((block, i) => {
            const key = `${i}`;
            switch (block.type) {
              case "heading":
                return block.level === 1 ? (
                  <h1
                    key={key}
                    className="font-display text-[32px] leading-10 font-semibold min-[900px]:text-[40px] min-[900px]:leading-[46px]"
                  >
                    <Inlines inlines={block.text} />
                  </h1>
                ) : block.level === 2 ? (
                  <h2
                    key={key}
                    id={block.id}
                    className="mt-4 scroll-mt-6 border-t border-line pt-5 text-[21px] leading-7 font-bold"
                  >
                    <Inlines inlines={block.text} />
                  </h2>
                ) : (
                  <h3
                    key={key}
                    id={block.id}
                    className="scroll-mt-6 text-[18px] leading-7 font-bold"
                  >
                    <Inlines inlines={block.text} />
                  </h3>
                );
              case "list":
                return <List key={key} list={block} />;
              case "table":
                return (
                  <div key={key} className="overflow-x-auto">
                    <table className="w-full border-collapse text-left text-[15px] leading-[22px]">
                      <thead>
                        <tr>
                          {block.head.map((cell, j) => (
                            // biome-ignore lint/suspicious/noArrayIndexKey: a static document parsed at build time; it never reorders
                            <th key={`${j}`} scope="col" className="border-b border-line py-2 pr-4">
                              <Inlines inlines={cell} />
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {block.rows.map((row, r) => (
                          // biome-ignore lint/suspicious/noArrayIndexKey: a static document parsed at build time; it never reorders
                          <tr key={`${r}`}>
                            {row.map((cell, j) => (
                              // biome-ignore lint/suspicious/noArrayIndexKey: a static document parsed at build time; it never reorders
                              <td key={`${j}`} className="border-b border-line py-2 pr-4 align-top">
                                <Inlines inlines={cell} />
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                );
              default:
                return (
                  <p key={key}>
                    <Inlines inlines={block.text} />
                  </p>
                );
            }
          })}
        </main>
      </div>
      <PublicFooter wide />
    </>
  );
}
