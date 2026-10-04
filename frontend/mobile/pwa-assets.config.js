import { defineConfig, minimal2023Preset } from "@vite-pwa/assets-generator/config";

// PWA icons are generated from the logo at build time (no PNGs in git). Maskable and Apple icons
// pad with the brand green so the mark stays inside the safe zone on any launcher shape.
const green = { background: "#2F5D3A" };

export default defineConfig({
  headLinkOptions: { preset: "2023" },
  preset: {
    ...minimal2023Preset,
    maskable: { ...minimal2023Preset.maskable, resizeOptions: green },
    apple: { ...minimal2023Preset.apple, resizeOptions: green },
  },
  images: ["public/icon.svg"],
});
