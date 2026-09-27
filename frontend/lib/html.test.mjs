// Run with: npm test (node --test; Node 23+ strips the TypeScript types).
import { test } from "node:test";
import assert from "node:assert/strict";
import { escapeHtml } from "./html.ts";

test("escapes the five characters that matter in HTML text and attributes", () => {
  assert.equal(escapeHtml(`</script><script>alert(1)</script> & "q" 'a'`),
    "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot; &#39;a&#39;");
  assert.equal(escapeHtml("plain"), "plain");
  assert.equal(escapeHtml("&amp;"), "&amp;amp;");
});
