// Small text helpers for the public pages.
//
// Pure and dependency-free so it can be tested with `node --test`.

import type { Taxonomy } from "@/lib/public-types";

/** A region slug as words: `bay-of-plenty` -> `Bay of Plenty` (mirrors api/fit.py). */
export function regionLabel(slug: string): string {
  const small = new Set(["of", "and", "de", "si"]);
  return slug
    .split("-")
    .map((w, i) => (i > 0 && small.has(w) ? w : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

/** A tag's label from the taxonomy, else the slug as words. */
export function tagLabel(taxonomy: Pick<Taxonomy, "tags"> | null, slug: string): string {
  const label = taxonomy?.tags.find((tag) => tag.slug === slug)?.label;
  return label || regionLabel(slug);
}

/** Free text for a meta description: whitespace collapsed, cut at a word near `max` characters. */
export function clip(text: string | null | undefined, max = 160): string {
  const flat = (text ?? "").replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  const cut = flat.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}
