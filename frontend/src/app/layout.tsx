import type { Metadata } from "next";
import { Archivo, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { Shell } from "@/components/shell";

// One variable font carrying two roles. Body sits at the normal width; display
// is the same file stretched via the `wdth` axis (see `.t-display` in
// globals.css), which keeps the page cohesive and costs one font download
// instead of two. Deliberately not Geist: a distinctive interface should not
// open in the same typeface as every other Next.js project.
const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  axes: ["wdth"],
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Kept",
  description:
    "Turns meeting transcripts into accountable execution and tracks what stays unresolved.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${archivo.variable} ${plexMono.variable}`}>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
