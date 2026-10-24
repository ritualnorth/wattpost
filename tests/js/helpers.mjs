// Pull named top-level functions / consts out of the classic UI scripts in
// solar_monitor/web/js so their pure logic can be tested without a browser.
// The scripts share one global scope and run DOM code at load, so they can't
// simply be imported. A scan that tracks brace depth finds each declaration.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "solar_monitor", "web", "js");

function sliceDecl(src, name) {
  const re = new RegExp(`^(?:async\\s+)?(?:function\\s+${name}\\b|const\\s+${name}\\s*=)`, "m");
  const m = re.exec(src);
  if (!m) throw new Error(`declaration not found: ${name}`);
  let depth = 0, seenBody = false;
  for (let i = m.index; i < src.length; i++) {
    const c = src[i];
    if (c === "{" || c === "(" || c === "[") { depth++; seenBody = true; }
    else if (c === "}" || c === ")" || c === "]") depth--;
    else if (c === ";" && depth === 0) return src.slice(m.index, i + 1);
    if (depth === 0 && seenBody && c === "}" && src.startsWith("function", m.index + (src.slice(m.index).startsWith("async") ? 6 : 0))) {
      return src.slice(m.index, i + 1);
    }
  }
  throw new Error(`unterminated declaration: ${name}`);
}

// load({file: "06-history.js", names: [...], globals: {...}}) -> object of the named values
export function load(file, names, globals = {}) {
  const src = readFileSync(join(JS_DIR, file), "utf8");
  const code = names.map((n) => sliceDecl(src, n)).join("\n") +
    `\n;({${names.join(",")}})`;
  return vm.runInNewContext(code, { Math, Date, Number, Array, Object, isFinite, ...globals });
}
