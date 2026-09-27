// Escaping for the few HTML documents built as strings rather than by React
// (the public 404, see app/(public)/[ui]/404/[[...path]]/route.ts).
//
// Pure and dependency-free so it can be tested with `node --test`.

const ENTITIES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Text safe inside an element or a double- or single-quoted attribute. */
export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (c) => ENTITIES[c]);
}
