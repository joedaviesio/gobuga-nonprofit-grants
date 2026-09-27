// Copyright © 2026 GoBuga Limited (formerly Proxy Matches Limited). All rights reserved.
//
// Root layout of the public pages: server-rendered, no client providers, no
// cookies, no third-party requests. `ui` is the language segment proxy.ts
// adds (see lib/public-routes.ts); visitors never see it in a URL.

import type { Metadata } from "next";
import "../public.css";
import { SiteFooter, SiteHeader } from "../_ui/chrome";
import { loadSite } from "@/lib/site";

export const metadata: Metadata = {
  title: { template: "%s | GoBuga", default: "GoBuga" },
};

// No page is built ahead of time: each is rendered on its first request and
// then served from cache (see `revalidate` in each page). Building none also
// means `next build` never needs the backend.
export async function generateStaticParams() {
  return [];
}

export default async function PublicLayout({
  children,
  params,
}: Readonly<{ children: React.ReactNode; params: Promise<{ ui: string }> }>) {
  const { ui } = await params;
  const site = await loadSite(ui);
  return (
    <html lang={site.lang}>
      <body>
        <a href="#main" className="skip-link">{site.t("skip")}</a>
        <SiteHeader site={site} />
        <main id="main" className="max-w-4xl mx-auto px-4 py-6">
          {children}
        </main>
        <SiteFooter site={site} />
      </body>
    </html>
  );
}
