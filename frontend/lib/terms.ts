// Terms of service go live the day GoBuga Limited approves the text.
// Until then the /terms route shows a "being finalised" notice and no page
// links to it. Flip NEXT_PUBLIC_TERMS_ENABLED=1 on the frontend service
// (build-time, Next.js inlines it) once the locale strings hold the
// approved wording.
export const TERMS_ENABLED = process.env.NEXT_PUBLIC_TERMS_ENABLED === "1";
