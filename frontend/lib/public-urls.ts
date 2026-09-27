// URLs for the public pages. Every link is a plain, constructible URL
// (doctrine 3): parameters in a fixed order, empty ones dropped, and the
// visitor's chosen language carried as `lang=` only when it is not the
// country's default.
//
// Pure and dependency-free so it can be tested with `node --test`.

export type Params = Record<string, string | number | null | undefined>;
export type SearchParams = Record<string, string | string[] | undefined>;

/** `path?a=1&b=2`, keeping the given key order, dropping empty values, `lang` last. */
export function href(path: string, params: Params = {}, lang: string | null = null): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (key === "lang" || value === null || value === undefined) continue;
    const text = String(value).trim();
    if (text !== "") query.set(key, text);
  }
  if (lang) query.set("lang", lang);
  const qs = query.toString();
  return qs ? `${path}?${qs}` : path;
}

/** The first non-empty value of each named parameter, in the order named. */
export function pick(search: SearchParams, names: readonly string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const name of names) {
    const raw = search[name];
    const value = (Array.isArray(raw) ? raw[0] : raw)?.trim();
    if (value) out[name] = value;
  }
  return out;
}

/** The query string for a backend call: same parameters, same order, no `lang`. */
export function queryString(params: Record<string, string>): string {
  return href("", params).replace(/^\?/, "");
}

/** Link to the grant index filtered by one facet, e.g. `/grants?tag=sport`. */
export function facetHref(name: string, value: string, lang: string | null): string {
  return href("/grants", { [name]: value }, lang);
}

/** A grant page reached from a fit list, carrying the fit parameters. */
export function grantHref(id: string, fit: Record<string, string>, lang: string | null): string {
  return href(`/grants/${encodeURIComponent(id)}`, fit, lang);
}

export interface Pagination {
  from: number;        // 1-based index of the first row shown; 0 when empty
  to: number;          // 1-based index of the last row shown
  prevOffset: number | null;
  nextOffset: number | null;
}

/** Previous and next offsets for plain `offset=`/`limit=` links. */
export function paginate(total: number, offset: number, limit: number, shown: number): Pagination {
  const safeLimit = Math.max(1, limit);
  return {
    from: shown > 0 ? offset + 1 : 0,
    to: offset + shown,
    prevOffset: offset > 0 ? Math.max(0, offset - safeLimit) : null,
    nextOffset: offset + shown < total ? offset + safeLimit : null,
  };
}
