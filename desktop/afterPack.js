// electron-builder never copies node_modules through extraResources, but the
// Next.js standalone server needs its own. Copy them in after packing, before
// the installer is assembled.
const fs = require("fs");
const path = require("path");

exports.default = async function afterPack(context) {
  const src = path.resolve(__dirname, "..", "viewer", ".next", "standalone", "node_modules");
  const dest = path.join(context.appOutDir, "resources", "viewer", "node_modules");
  if (!fs.existsSync(src)) throw new Error(`missing ${src}: run npm run build:viewer first`);
  fs.cpSync(src, dest, { recursive: true, dereference: true });
  console.log(`  • copied viewer node_modules → ${dest}`);
};
