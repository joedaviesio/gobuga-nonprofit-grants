// Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono, DM_Sans, Inter } from "next/font/google";
import "../globals.css";
import AuthGate from "@/app/auth-gate";
import { HeaderLogout } from "@/app/header-logout";
import { LanguageSwitcher } from "@/app/language-switcher";
import { I18nProvider } from "@/lib/i18n";

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

// Root layout of the signed-in workspace, and of the privacy and terms pages,
// which use the workspace's client-side i18n. The public pages have their
// own server-rendered root layout in app/(public)/[ui]/layout.tsx.
export default function WorkspaceLayout({
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
        <I18nProvider>
          <AuthGate>
            <header className="border-b border-stone-200 px-6 py-4 flex items-center justify-between">
              <Link href="/workspace" className="flex items-center gap-2">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src="/gobuga-wordmark.svg" alt="gobuga.org" className="h-9 w-auto" />
              </Link>
              <div className="flex items-center gap-3">
                <LanguageSwitcher />
                <HeaderLogout />
              </div>
            </header>
            <main>{children}</main>
          </AuthGate>
        </I18nProvider>
      </body>
    </html>
  );
}
