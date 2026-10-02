import "./globals.css";

/** @type {import("next").Metadata} */
export const metadata = {
  title: { template: "%s · GharBasket Console", default: "GharBasket Console" },
  icons: { icon: "/icon.svg" },
  robots: { index: false },
};

/** @param {{ children: import("react").ReactNode }} props */
export default function RootLayout({ children }) {
  return (
    <html lang="en-IN">
      <body className="bg-bg text-fg antialiased">{children}</body>
    </html>
  );
}
