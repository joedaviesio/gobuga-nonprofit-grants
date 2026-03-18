import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import AuthGate from "./auth-gate";
import { HeaderLogout } from "./header-logout";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "GoBuga",
  description: "AI-powered grant scanning and submission platform for nonprofits",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <AuthGate>
          <header className="border-b border-stone-200 px-6 py-3 flex items-center justify-between">
            <a href="/" className="flex items-center gap-2">
              <span className="text-lg font-bold text-stone-800">GoBuga</span>
              <span className="text-xs text-stone-400 font-mono">Grant Scanner</span>
            </a>
            <HeaderLogout />
          </header>
          <main>{children}</main>
        </AuthGate>
      </body>
    </html>
  );
}
