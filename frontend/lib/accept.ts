// Content negotiation for public page URLs (doctrine 4): a page URL asked
// for with `Accept: application/json` gets its JSON twin.
//
// Pure and dependency-free so it can be tested with `node --test`.

interface MediaRange {
  type: string;
  subtype: string;
  q: number;
}

function parseAccept(header: string): MediaRange[] {
  const ranges: MediaRange[] = [];
  for (const part of header.split(",")) {
    const [range, ...params] = part.split(";");
    const [type, subtype] = range.trim().toLowerCase().split("/");
    if (!type || !subtype) continue;
    let q = 1;
    for (const param of params) {
      const [key, value] = param.split("=").map((s) => s.trim().toLowerCase());
      if (key === "q") {
        const parsed = Number(value);
        // RFC 9110: a quality value is 0 to 1; anything else is ignored.
        q = Number.isFinite(parsed) && parsed >= 0 && parsed <= 1 ? parsed : 1;
      }
    }
    ranges.push({ type, subtype, q });
  }
  return ranges;
}

/** Quality the client gives `type/subtype`: the most specific matching range wins. */
function qualityOf(ranges: MediaRange[], type: string, subtype: string): number {
  let best = -1;
  let bestSpecificity = -1;
  for (const r of ranges) {
    let specificity = -1;
    if (r.type === type && r.subtype === subtype) specificity = 2;
    else if (r.type === type && r.subtype === "*") specificity = 1;
    else if (r.type === "*" && r.subtype === "*") specificity = 0;
    if (specificity > bestSpecificity) {
      bestSpecificity = specificity;
      best = r.q;
    }
  }
  return best < 0 ? 0 : best;
}

/**
 * True only when the Accept header rates application/json strictly above
 * text/html. A browser (text/html first), `*\/*` (curl's default), a missing
 * header and a tie all get the HTML page.
 */
export function prefersJson(accept: string | null | undefined): boolean {
  if (!accept) return false;
  const ranges = parseAccept(accept);
  const json = qualityOf(ranges, "application", "json");
  const html = qualityOf(ranges, "text", "html");
  return json > 0 && json > html;
}
