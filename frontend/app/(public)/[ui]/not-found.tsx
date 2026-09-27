// Fallback only. proxy.ts sends unknown paths, grants, funders and months
// to the 404 route (404/[[...path]]/route.ts), which renders complete HTML.
// A page reaches notFound() only if that check could not be made; Next then
// returns a 404 whose text the browser renders from this file. Next gives a
// not-found file no params, so its text is in the country's default language.

import { loadSite } from "@/lib/site";

export default async function PublicNotFound() {
  const site = await loadSite("ui-auto");
  return (
    <div>
      <h1 className="text-3xl font-bold mb-3">{site.t("not_found_title")}</h1>
      <p className="mb-4">{site.t("not_found_body")}</p>
      <ul className="list-disc pl-6">
        <li><a href="/grants">{site.t("see_all")}</a></li>
        <li><a href="/funders">{site.t("nav_funders")}</a></li>
        <li><a href="/">{site.t("brand_home")}</a></li>
      </ul>
    </div>
  );
}
