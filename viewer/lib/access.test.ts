// Run: npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { crossSite, localHost, topLevelPageVisit } from "./access.ts";

test("only loopback names are local", () => {
  for (const h of ["127.0.0.1:3217", "localhost:3217", "[::1]:3217", "LOCALHOST", "127.0.0.1"]) assert.ok(localHost(h), h);
  for (const h of ["attacker.example:3217", "127.0.0.2", "0.0.0.0", "foo.localhost", "localhost.", "", null]) assert.ok(!localHost(h), String(h));
});

test("requests made by other sites are refused, except opening a page in the tab", () => {
  assert.ok(crossSite("cross-site") && crossSite("same-site"));
  assert.ok(!crossSite("same-origin") && !crossSite("none") && !crossSite(null));
  const nav = (dest: string, method = "GET") => ({ method, headers: new Headers({ "sec-fetch-mode": "navigate", "sec-fetch-dest": dest }) });
  assert.ok(topLevelPageVisit(nav("document"), "/s/abc"));
  assert.ok(!topLevelPageVisit(nav("document"), "/api/sessions/abc"));
  assert.ok(!topLevelPageVisit(nav("image"), "/s/abc"));
  assert.ok(!topLevelPageVisit(nav("document", "POST"), "/s/abc"));
  assert.ok(!topLevelPageVisit(nav("iframe"), "/s/abc"));
});
