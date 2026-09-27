// schema.org JSON-LD for the public pages, and its safe serialisation.
//
// Doctrine 4: JSON-LD states nothing the visible page does not state. Every
// field below is one the page renders as text, and a field the record leaves
// empty is left out rather than guessed.
//
// Pure and dependency-free so it can be tested with `node --test`.

type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

/**
 * JSON for an inline <script type="application/ld+json"> block.
 *
 * Grant titles come from funder websites and are untrusted. JSON.stringify
 * leaves `</script>` intact, which would end the block and let the rest run
 * as HTML. Escaping `<`, `>` and `&` as <, > and & keeps the
 * text inert in HTML while any JSON parser reads back the same string.
 * U+2028 and U+2029 are escaped too, for older JavaScript parsers.
 */
export function serializeJsonLd(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

/** The fields of a public record (see api/public_data.py) that JSON-LD uses. */
export interface GrantForJsonLd {
  id: string;
  title: string | null;
  summary: string | null;
  canonical_url: string;
  funder: { name: string | null; url: string | null };
  deadline: { state: string | null; date: string | null };
  amount: { min: number | null; max: number | null; currency: string | null };
}

function positive(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function compact(obj: { [key: string]: Json | undefined }): { [key: string]: Json } {
  const out: { [key: string]: Json } = {};
  for (const [key, value] of Object.entries(obj)) {
    if (value !== undefined && value !== null && value !== "") out[key] = value;
  }
  return out;
}

/**
 * A MonetaryGrant: name, description, identifier, url, the funder as an
 * Organization, and the amount as a MonetaryAmount whose validThrough is the
 * closing date. schema.org's Grant has no deadline property of its own;
 * MonetaryAmount.validThrough ("the date after which the price is no longer
 * available") is the nearest standard field.
 */
export function grantJsonLd(grant: GrantForJsonLd): { [key: string]: Json } {
  const min = positive(grant.amount.min);
  const max = positive(grant.amount.max);
  const closing = grant.deadline.state === "dated" ? grant.deadline.date : null;
  const hasAmount = (min !== null || max !== null) && !(min !== null && max !== null && min > max);
  let amount: { [key: string]: Json } | undefined;
  if (hasAmount || closing) {
    amount = compact({
      "@type": "MonetaryAmount",
      currency: hasAmount ? grant.amount.currency : undefined,
      minValue: hasAmount ? min : undefined,
      maxValue: hasAmount ? max : undefined,
      validThrough: closing ?? undefined,
    });
  }
  return compact({
    "@context": "https://schema.org",
    "@type": "MonetaryGrant",
    "@id": grant.canonical_url,
    identifier: grant.id,
    name: grant.title,
    description: grant.summary,
    url: grant.canonical_url,
    funder: grant.funder.name
      ? compact({ "@type": "Organization", name: grant.funder.name, url: grant.funder.url ?? undefined })
      : undefined,
    amount,
  });
}

export interface DatasetForJsonLd {
  name: string;
  description: string;
  url: string;
  licence: string;
  publisherName: string;
  publisherUrl: string;
  spatialCoverage: string;
  dateModified: string | null;
  downloads: { url: string; encodingFormat: string; name: string }[];
}

/** A Dataset describing the published pool, with its JSON downloads. */
export function datasetJsonLd(d: DatasetForJsonLd): { [key: string]: Json } {
  return compact({
    "@context": "https://schema.org",
    "@type": "Dataset",
    name: d.name,
    description: d.description,
    url: d.url,
    license: compact({ "@type": "CreativeWork", name: d.licence }),
    isAccessibleForFree: true,
    publisher: compact({ "@type": "Organization", name: d.publisherName, url: d.publisherUrl }),
    spatialCoverage: d.spatialCoverage,
    dateModified: d.dateModified ?? undefined,
    distribution: d.downloads.map((dl) => compact({
      "@type": "DataDownload",
      name: dl.name,
      encodingFormat: dl.encodingFormat,
      contentUrl: dl.url,
    })),
  });
}
