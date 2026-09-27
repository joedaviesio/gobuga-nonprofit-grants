// /subscribe: one email field, posted as a plain form to /api/subscribe,
// which answers with a redirect back here carrying ?state=sent|invalid|limited.
// Rendered on request because it reads that state; it fetches nothing but the
// (cached) country config.

import type { Metadata } from "next";
import { LanguageLinks, SubscribeForm } from "../../_ui/chrome";
import type { PublicKey } from "@/lib/public-i18n";
import { pick, type SearchParams } from "@/lib/public-urls";
import { loadSite, pageMetadata } from "@/lib/site";

// Reads the query string, so it is rendered on every request.
export const dynamic = "force-dynamic";

type Props = { params: Promise<{ ui: string }>; searchParams: Promise<SearchParams> };

const STATES: Record<string, PublicKey> = {
  sent: "subscribe_sent",
  invalid: "subscribe_invalid",
  limited: "subscribe_limited",
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { ui } = await params;
  const site = await loadSite(ui);
  return pageMetadata(site, {
    path: "/subscribe",
    title: site.t("subscribe_title"),
    description: site.t("subscribe_what"),
  });
}

export default async function SubscribeRoute({ params, searchParams }: Props) {
  const { ui } = await params;
  const site = await loadSite(ui);
  const state = pick(await searchParams, ["state"]).state;
  const message = state ? STATES[state] : undefined;
  return (
    <div>
      <LanguageLinks site={site} path="/subscribe" />
      <h1 className="text-3xl font-bold mb-3">{site.t("subscribe_title")}</h1>
      {message && (
        <p role="status" className={`border-l-4 p-3 mb-4 ${state === "sent" ? "border-green-700 bg-green-50" : "border-red-600 bg-red-50"}`}>
          {site.t(message)}
        </p>
      )}
      <p className="mb-2">{site.t("subscribe_what")}</p>
      <p className="mb-4">{site.t("subscribe_how")}</p>
      <SubscribeForm site={site} id="subscribe-email" />
    </div>
  );
}
