import tailwindcss from "@tailwindcss/vite";
import { mergeConfig } from "vite";

/** @type {import("@storybook/react-vite").StorybookConfig} */
const config = {
  framework: "@storybook/react-vite",
  stories: ["../src/**/*.stories.@(js|jsx)"],
  addons: ["@storybook/addon-a11y"],
  async viteFinal(viteConfig) {
    return mergeConfig(viteConfig, { plugins: [tailwindcss()] });
  },
};

export default config;
