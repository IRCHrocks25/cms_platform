const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

test("an empty block image alt stays empty after an editor reload", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "editor.js"), "utf8");
  const start = source.indexOf("  function cmsIsBlankHtml(value) {");
  const end = source.indexOf("  function setStatus(", start);
  assert.ok(start >= 0 && end > start, "editor value helpers are available");

  const instance = { id: "blk_abcd1234", fields: {} };
  const helpers = new Function(
    "content", "BLOCKS", "BLOCK_DEFAULTS", "findInstance",
    source.slice(start, end) + "return { getValue, setValue };",
  )({}, true, { feature: { photo_alt: "Kitchen" } },
    (id) => id === instance.id ? instance : null);
  instance.type = "feature";

  helpers.setValue("blk_abcd1234.photo_alt", "");
  assert.equal(instance.fields.photo_alt, "");
  assert.equal(helpers.getValue("blk_abcd1234.photo_alt"), "");

  helpers.setValue("blk_abcd1234.title", "");
  assert.equal(instance.fields.title, undefined);
});
