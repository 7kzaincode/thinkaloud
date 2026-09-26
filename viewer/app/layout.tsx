import type { Metadata } from "next";
import { Instrument_Sans, JetBrains_Mono, Newsreader } from "next/font/google";
import "./globals.css";

const sans = Instrument_Sans({ subsets: ["latin"], variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });
const serif = Newsreader({ subsets: ["latin"], style: ["normal", "italic"], variable: "--font-serif" });

export const metadata: Metadata = {
  title: "thinkaloud review",
  description: "Review think-aloud computer-use trajectories",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${serif.variable}`}>
      <body>{children}</body>
    </html>
  );
}
