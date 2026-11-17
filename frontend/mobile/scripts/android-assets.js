// `pnpm android:assets`: builds the Android launcher icon and splash screens from the PWA logo
// (public/icon.svg). Writes the 1024 px source images to assets/ and the density folders under
// android/app/src/main/res/. Re-run it after the logo changes and commit the result.
//
// Why a script and not @capacitor/assets: that tool was last released in March 2024, pulls an old
// sharp, and cannot make our adaptive icon (green background, mark only in the foreground).
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const res = join(root, "android/app/src/main/res");
const assets = join(root, "assets");

// Brand colours: green and kraft from public/icon.svg, charcoal-900 from packages/ui tokens.css.
const GREEN = "#2F5D3A";
const KRAFT = "#FBF7F0";
const CHARCOAL = "#1A1714";

// The mark is public/icon.svg without its rounded green tile: amber leaf, cream basket.
const logo = await readFile(join(root, "public/icon.svg"), "utf8");
const markPaths = logo.match(/<g fill="none".*<\/g>/s)?.[0];
if (!markPaths) throw new Error("public/icon.svg no longer has the expected <g> mark");

// The mark spans about x 5-59, y 4-58 of the 64-unit logo grid. Centre it on a 108-unit adaptive
// canvas at a size that stays inside the 66-unit circle every launcher mask guarantees.
const mark = (scale, colour) => {
  const g = colour ? markPaths.replace(/stroke="#[0-9a-f]{6}"/gi, `stroke="${colour}"`) : markPaths;
  return `<g transform="translate(${54 - 32 * scale} ${54 - 31 * scale}) scale(${scale})">${g}</g>`;
};
const svg = (body) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108">${body}</svg>`;

const render = (markup, px) =>
  sharp(Buffer.from(markup.replace("<svg ", `<svg width="${px}" height="${px}" `)), {
    density: 300,
  })
    .resize(px, px)
    .png();

const layers = {
  foreground: svg(mark(1.0)),
  monochrome: svg(mark(1.0, "#000000")),
  // Pre-Android-8 launchers: the PWA tile itself, with its rounded corners.
  legacy: logo,
  round: svg(`<circle cx="54" cy="54" r="54" fill="${GREEN}"/>${mark(0.95)}`),
  // Full-bleed square for the 1024 px master and the Play Store listing (the store applies the mask).
  master: svg(`<rect width="108" height="108" fill="${GREEN}"/>${mark(1.0)}`),
};

const DENSITIES = { mdpi: 1, hdpi: 1.5, xhdpi: 2, xxhdpi: 3, xxxhdpi: 4 };
const write = async (path, image) => {
  await mkdir(dirname(path), { recursive: true });
  await image.toFile(path);
};

// 1. Launcher icons.
await write(join(assets, "icon-1024.png"), render(layers.master, 1024));
for (const [name, k] of Object.entries(DENSITIES)) {
  const dir = join(res, `mipmap-${name}`);
  await write(join(dir, "ic_launcher.png"), render(layers.legacy, 48 * k));
  await write(join(dir, "ic_launcher_round.png"), render(layers.round, 48 * k));
  await write(join(dir, "ic_launcher_foreground.png"), render(layers.foreground, 108 * k));
  await write(join(dir, "ic_launcher_monochrome.png"), render(layers.monochrome, 108 * k));
}
const adaptive = `<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background"/>
    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>
    <monochrome android:drawable="@mipmap/ic_launcher_monochrome"/>
</adaptive-icon>
`;
await writeFile(join(res, "mipmap-anydpi-v26/ic_launcher.xml"), adaptive);
await writeFile(join(res, "mipmap-anydpi-v26/ic_launcher_round.xml"), adaptive);
await writeFile(
  join(res, "values/ic_launcher_background.xml"),
  `<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">${GREEN}</color>
</resources>
`,
);
// Capacitor's template ships a grid drawable and a robot foreground that nothing references now.
await rm(join(res, "drawable/ic_launcher_background.xml"), { force: true });
await rm(join(res, "drawable-v24"), { recursive: true, force: true });

// 2. Splash: the tile centred on the theme background, light and dark. Android picks the file by
// orientation, night mode and density; the -night folders are used when the phone is in dark mode.
const splash = async (w, h, bg) => {
  const tile = Math.round(Math.min(w, h) * 0.26);
  const tileSvg = logo.replace("<svg ", `<svg width="${tile}" height="${tile}" `);
  const tilePng = await sharp(Buffer.from(tileSvg), { density: 300 })
    .resize(tile, tile)
    .png()
    .toBuffer();
  return sharp({ create: { width: w, height: h, channels: 3, background: bg } })
    .composite([{ input: tilePng, gravity: "centre" }])
    .png();
};
const PORT = {
  mdpi: [320, 480],
  hdpi: [480, 800],
  xhdpi: [720, 1280],
  xxhdpi: [960, 1600],
  xxxhdpi: [1280, 1920],
};
const themes = [
  ["", KRAFT],
  ["-night", CHARCOAL],
];
for (const [night, bg] of themes) {
  await write(join(res, `drawable${night}/splash.png`), await splash(480, 320, bg));
  for (const [name, [w, h]] of Object.entries(PORT)) {
    await write(join(res, `drawable-port${night}-${name}/splash.png`), await splash(w, h, bg));
    await write(join(res, `drawable-land${night}-${name}/splash.png`), await splash(h, w, bg));
  }
}
await write(join(assets, "splash-2732.png"), await splash(2732, 2732, KRAFT));
await write(join(assets, "splash-dark-2732.png"), await splash(2732, 2732, CHARCOAL));

console.log("Android icon and splash resources written. Review the diff, then commit.");
