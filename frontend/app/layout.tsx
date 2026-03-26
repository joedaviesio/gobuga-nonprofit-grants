import type { Metadata } from "next";
import { Geist, Geist_Mono, DM_Sans, Inter } from "next/font/google";
import "./globals.css";
import AuthGate from "./auth-gate";
import { HeaderLogout } from "./header-logout";
import { HeaderTierLabel } from "./header-tier-label";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const dmSans = DM_Sans({
  variable: "--font-dm-sans",
  subsets: ["latin"],
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "gobuga",
  description: "Helps with nonprofit grants",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <link rel="stylesheet" href="https://use.typekit.net/czn0xnx.css" />
      </head>
      <body className={`${geistSans.variable} ${geistMono.variable} ${dmSans.variable} ${inter.variable} antialiased`}>
        <AuthGate>
          <header className="border-b border-stone-200 px-6 py-3 flex items-center justify-between">
            <a href="/" className="flex items-center gap-2">
              <span className="text-lg font-bold text-stone-800" style={{ fontFamily: 'var(--font-geist-sans)' }}>gobuga</span>
              <HeaderTierLabel />
            </a>
            <HeaderLogout />
          </header>
          <main>{children}</main>
        </AuthGate>
      </body>
    </html>
  );
}
