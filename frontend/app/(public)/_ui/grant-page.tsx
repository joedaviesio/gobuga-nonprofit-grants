// The canonical grant page, shared by /grants/[id] (cached) and the same
// page reached from a fit URL (rendered on request, with "why this fits").

import type { Metadata } from "next";
import { JsonLd, LanguageLinks } from "./chrome";
import {
  amountString, Deadline, deadlineString, InstantDate, statusLabel, Tx, Verified,
} from "./grant-bits";
import { BackendUnavailable, getCached } from "@/lib/backend";
import { formatInstantDate } from "@/lib/format";
import { grantJsonLd } from "@/lib/jsonld";
import { clip, regionLabel, tagLabel } from "@/lib/labels";
import type { PublicKey } from "@/lib/public-i18n";
import type { GrantRecord, Taxonomy } from "@/lib/public-types";
import { facetHref, href } from "@/lib/public-urls";
import { pageMetadata, type Site } from "@/lib/site";

const PROVENANCE_FIELDS = ["deadline", "amount", "funder", "eligibility"] as const;

/** The public record for an ID, or null when the backend has no such grant. */
export async function loadGrant(id: string): Promise<GrantRecord | null> {
  const res = await getCached<GrantRecord>(`/api/v1/opportunities/${encodeURIComponent(id)}`);
  if (res.status === 404 || res.status === 400) return null;
  if (!res.body) throw new BackendUnavailable(`grant ${id}: ${res.status}`);
  return res.body;
}

export function grantMetadata(site: Site, grant: GrantRecord): Metadata {
  const path = `/grants/${grant.id}`;
  const title = grant.funder.name ? `${grant.title} (${grant.funder.name})` : grant.title || grant.id;
  const verified = formatInstantDate(grant.deadline.verified_at || grant.verified_at, site.locale, site.timezone);
  const facts = [
    grant.funder.name,
    amountString(site, grant.amount),
    deadlineString(site, grant),
    verified ? site.t("verified_line", { date: verified }) : null,
  ]
    // Some locales end a date with a period ("20 октября 2026 г."); join without doubling it.
    .filter((part): part is string => Boolean(part))
    .map((part) => part.replace(/\.$/, ""))
    .join(". ");
  return pageMetadata(site, {
    path,
    title,
    description: clip(`${facts}. ${grant.summary ?? ""}`),
    json: `${path}.json`,
    noindex: grant.status === "stale",
  });
}

export interface FitReasons {
  why: string[] | null;          // null: the grant is not in the ranked list
  error: string | null;          // a message when the fit parameters were invalid
  params: Record<string, string>;
}

export function GrantPage({ site, grant, taxonomy, fit }: {
  site: Site;
  grant: GrantRecord;
  taxonomy: Taxonomy | null;
  fit?: FitReasons;
}) {
  const t = site.t;
  const path = `/grants/${grant.id}`;
  const canonical = site.base + href(path, {}, site.langs.carry);
  const entities = grant.eligible_entities
    .map((slug) => {
      const key = `entity.${slug}` as PublicKey;
      const label = t(key);
      return label === key ? slug : label;
    })
    .join(", ");
  const provenance = PROVENANCE_FIELDS.filter((f) => grant.provenance[f]?.excerpt || grant.provenance[f]?.source_url);

  return (
    <article>
      <LanguageLinks site={site} path={path} />
      <p className="text-sm font-semibold uppercase tracking-wide text-slate-600">
        {statusLabel(site, grant.status)}
      </p>
      <h1 className="text-3xl font-bold leading-tight mb-3 break-words">{grant.title || grant.id}</h1>

      {grant.status === "stale" && (
        <section role="note" aria-labelledby="stale-title" className="border-l-4 border-amber-500 bg-amber-50 p-3 mb-4">
          <h2 id="stale-title" className="font-semibold">{t("stale_title")}</h2>
          <p>{t("stale_notice")}</p>
        </section>
      )}
      {grant.status === "closed" && (
        <p role="note" className="border-l-4 border-slate-500 bg-slate-50 p-3 mb-4">{t("closed_notice")}</p>
      )}

      <p className="text-lg mb-1">
        <strong><Deadline site={site} grant={grant} /></strong>
      </p>
      <p className="text-lg mb-4">
        <Verified site={site} grant={grant} />
      </p>

      <p className="mb-2">
        <a href={`/out/${encodeURIComponent(grant.id)}`} rel="nofollow" className="text-lg font-semibold">
          {/* A closed or unverified listing is not an invitation to apply. */}
          {t(grant.status === "live" ? "apply_link" : "funder_page_link")}
        </a>
      </p>
      <p className="mb-6">
        <a href="/register">{t("workspace_link")}</a>
      </p>

      {fit && <WhyThisFits site={site} fit={fit} />}

      <h2 className="text-2xl font-semibold mt-6 mb-3">{t("grant_facts")}</h2>
      <dl className="facts">
        <dt>{t("f_funder")}</dt>
        <dd>
          {grant.funder.slug
            ? <a href={site.link(`/funders/${grant.funder.slug}`)}>{grant.funder.name}</a>
            : grant.funder.name ?? t("not_stated")}
        </dd>
        <dt>{t("f_amount")}</dt>
        <dd>{amountString(site, grant.amount)}</dd>
        <dt>{t("f_deadline")}</dt>
        <dd><Deadline site={site} grant={grant} /></dd>
        <dt>{t("f_status")}</dt>
        <dd>{statusLabel(site, grant.status)}</dd>
        <dt>{t("f_region")}</dt>
        <dd>
          {grant.region.length === 0 ? t("not_stated") : grant.region.map((slug, i) => (
            <span key={slug}>{i > 0 && ", "}<a href={facetHref("region", slug, site.langs.carry)}>{regionLabel(slug)}</a></span>
          ))}
        </dd>
        <dt>{t("f_tags")}</dt>
        <dd>
          {grant.tags.length === 0 ? t("not_stated") : grant.tags.map((slug, i) => (
            <span key={slug}>{i > 0 && ", "}<a href={facetHref("tag", slug, site.langs.carry)}>{tagLabel(taxonomy, slug)}</a></span>
          ))}
        </dd>
        <dt>{t("f_entities")}</dt>
        <dd>{entities || t("not_stated")}</dd>
        <dt>{t("f_eligibility")}</dt>
        <dd className="prose-text">{grant.eligibility || t("not_stated")}</dd>
        <dt>{t("f_id")}</dt>
        <dd><code>{grant.id}</code></dd>
        {grant.last_seen && (
          <>
            <dt>{t("f_last_seen")}</dt>
            <dd><InstantDate site={site} iso={grant.last_seen} /></dd>
          </>
        )}
      </dl>

      <h2 className="text-2xl font-semibold mt-8 mb-2">{t("f_summary")}</h2>
      <p className="prose-text">{grant.summary || t("not_stated")}</p>

      <section aria-labelledby="provenance" className="mt-8">
        <h2 id="provenance" className="text-2xl font-semibold mb-2">{t("provenance_title")}</h2>
        {provenance.length === 0 ? <p>{t("provenance_none")}</p> : <p className="mb-3">{t("provenance_intro")}</p>}
        {provenance.map((field) => {
          const p = grant.provenance[field];
          return (
            <div key={field} className="mb-4">
              <h3 className="font-semibold">{t(`prov.${field}` as PublicKey)}</h3>
              {p.excerpt && (
                <blockquote className="excerpt" cite={p.source_url ?? undefined}>
                  <p>{p.excerpt}</p>
                </blockquote>
              )}
              <p className="text-sm text-slate-700">
                {p.source_url && (
                  <>
                    {t("prov_source")}: <a href={p.source_url} rel="nofollow" className="break-all">{p.source_url}</a>
                  </>
                )}
                {p.source_url && p.verified_at && " · "}
                {p.verified_at && (
                  <Tx site={site} k="prov_checked" values={{ date: <InstantDate site={site} iso={p.verified_at} /> }} />
                )}
              </p>
            </div>
          );
        })}
      </section>

      {grant.status === "closed" && grant.related && grant.related.length > 0 && (
        <section aria-labelledby="related" className="mt-8">
          <h2 id="related" className="text-2xl font-semibold mb-2">{t("related_title")}</h2>
          <ul className="list-disc pl-6">
            {grant.related.map((r) => (
              <li key={r.id}><a href={site.link(`/grants/${encodeURIComponent(r.id)}`)}>{r.title || r.id}</a></li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="cite" className="mt-8">
        <h2 id="cite" className="text-xl font-semibold mb-1">{t("cite_title")}</h2>
        <p>{t("cite_body")} <a href={canonical} className="break-all">{canonical}</a></p>
        <p className="mt-2"><a href={`${path}.json`} type="application/json">{t("json_link")}</a></p>
      </section>

      <JsonLd data={grantJsonLd(grant)} />
    </article>
  );
}

function WhyThisFits({ site, fit }: { site: Site; fit: FitReasons }) {
  return (
    <section aria-labelledby="why" className="border border-slate-300 rounded-md p-4 mb-6">
      <h2 id="why" className="text-xl font-semibold mb-2">{site.t("why_title")}</h2>
      {fit.error && <p role="alert">{fit.error}</p>}
      {!fit.error && fit.why === null && <p>{site.t("why_missing")}</p>}
      {fit.why && fit.why.length > 0 && (
        <ul className="list-disc pl-6">
          {fit.why.map((reason) => <li key={reason}>{reason}</li>)}
        </ul>
      )}
      <p className="mt-2"><a href={site.link("/fit", fit.params)}>{site.t("why_back")}</a></p>
    </section>
  );
}
