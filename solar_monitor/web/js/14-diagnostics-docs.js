// WattPost UI, part 14: diagnostics log tail, docs, daemon restart.
// Plain script (no modules): the numbered files in web/js/ are loaded in order by
// index.html and share one global scope, exactly as the old single app.js did.

// ---------- diagnostics (log tail) ----------
let diagTimer = null;
const DIAG_REFRESH_MS = 4000;
function startDiagTimer() {
  if (diagTimer) return;
  refreshDiagLog();
  diagTimer = setInterval(refreshDiagLog, DIAG_REFRESH_MS);
}
function stopDiagTimer() {
  if (diagTimer) { clearInterval(diagTimer); diagTimer = null; }
}

function renderInline(s) {
  let out = escHtml(s);
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
  return out;
}
function renderMarkdown(src) {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const ln = lines[i];
    // Fenced code block
    if (/^```/.test(ln)) {
      const lang = ln.slice(3).trim();
      i++;
      const buf = [];
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++; // skip closing ```
      out.push(`<pre><code data-lang="${escHtml(lang)}">${escHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }
    // Heading
    const h = ln.match(/^(#{1,6})\s+(.+)$/);
    if (h) {
      const level = h[1].length;
      out.push(`<h${level}>${renderInline(h[2])}</h${level}>`);
      i++; continue;
    }
    // Horizontal rule
    if (/^---+\s*$/.test(ln)) { out.push("<hr/>"); i++; continue; }
    // Pipe table (header | --- | row …)
    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|[-:\s|]+\|\s*$/.test(lines[i + 1])) {
      const split = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map(s => s.trim());
      const head = split(ln);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(split(lines[i])); i++;
      }
      const thead = head.map(c => `<th>${renderInline(c)}</th>`).join("");
      const tbody = rows.map(r => `<tr>${r.map(c => `<td>${renderInline(c)}</td>`).join("")}</tr>`).join("");
      out.push(`<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`);
      continue;
    }
    // Blockquote
    if (/^>\s?/.test(ln)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, "")); i++;
      }
      out.push(`<blockquote>${renderMarkdown(buf.join("\n"))}</blockquote>`);
      continue;
    }
    // Ordered list, captures multi-line items: any subsequent line
    // indented by 2+ spaces (and not a new marker) is folded into the
    // current item. Without this, hard-wrapped 80-char list items
    // render as half-bullet/half-paragraph with mismatched fonts.
    if (/^\s*\d+\.\s+/.test(ln)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*\d+\.\s+(.+)$/);
        if (!m) break;
        let buf = m[1];
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+|^\s*\d+\.\s+/.test(lines[i])) {
          buf += " " + lines[i].trim();
          i++;
        }
        items.push(buf);
      }
      out.push(`<ol>${items.map(it => `<li>${renderInline(it)}</li>`).join("")}</ol>`);
      continue;
    }
    // Unordered list, same continuation rule as ordered.
    if (/^\s*[-*]\s+/.test(ln)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)[-*]\s+(.+)$/);
        if (!m) break;
        const indent = m[1].length;
        let buf = m[2];
        i++;
        // Consume continuation lines indented strictly more than the
        // marker. Stops at blank line, new marker, or unindented text.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+|^\s*\d+\.\s+/.test(lines[i])) {
          buf += " " + lines[i].trim();
          i++;
        }
        items.push(buf);
        // Allow blank line between siblings without exiting the list.
        if (i < lines.length && /^\s*$/.test(lines[i])
            && i + 1 < lines.length && /^\s*[-*]\s+/.test(lines[i + 1])) {
          i++;
        }
        void indent;
      }
      out.push(`<ul>${items.map(it => `<li>${renderInline(it)}</li>`).join("")}</ul>`);
      continue;
    }
    // Blank line
    if (/^\s*$/.test(ln)) { i++; continue; }
    // Paragraph, gather consecutive non-blank, non-special lines.
    const para = [ln];
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|>\s?|---|\s*[-*]\s|\s*\d+\.\s|```)/.test(lines[i])) {
      para.push(lines[i]); i++;
    }
    out.push(`<p>${renderInline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}

let docsIndex = null;  // cached topic list
let docsLoaded = false;

async function onEnterDocs(slug) {
  if (!docsLoaded) {
    try {
      const r = await fetch("/web/docs/index.appliance.json");
      docsIndex = await r.json();
      docsLoaded = true;
    } catch (e) {
      $("#docs-nav").innerHTML = `<div class="settings-empty">Could not load docs index: ${e.message}</div>`;
      return;
    }
  }
  renderDocsNav(slug);
  if (slug) {
    loadDocPage(slug);
  } else if (docsIndex.topics?.length) {
    // No slug, auto-open the first topic so the user isn't staring at
    // an empty pane.
    window.location.hash = `#/docs/${docsIndex.topics[0].slug}`;
  }
}

function renderDocsNav(activeSlug) {
  const host = $("#docs-nav");
  if (!host || !docsIndex) return;
  // Group by section in document order.
  const sections = [];
  for (const t of docsIndex.topics) {
    let s = sections.find(x => x.name === t.section);
    if (!s) { s = { name: t.section, topics: [] }; sections.push(s); }
    s.topics.push(t);
  }
  host.innerHTML = sections.map(s => `
    <div class="docs-nav-section">${escHtml(s.name)}</div>
    ${s.topics.map(t => `
      <a class="docs-nav-link ${t.slug === activeSlug ? "active" : ""}"
         href="#/docs/${encodeURIComponent(t.slug)}">${escHtml(t.title)}</a>`).join("")}
  `).join("");
}

async function loadDocPage(slug) {
  const host = $("#docs-content");
  if (!host) return;
  host.innerHTML = `<p class="docs-placeholder">Loading…</p>`;
  try {
    // release-notes is special: prefer the live cache from
    // /api/releases/changelog so users can see "what's in 0.0.3"
    // BEFORE they install 0.0.3. The bundled .md only knows about
    // versions <= the running release. Fall back to bundled if the
    // cache is empty (first boot, or offline since boot).
    let md = null;
    if (slug === "release-notes") {
      try {
        const r = await fetch("/api/releases/changelog");
        if (r.ok && r.status !== 204) md = await r.text();
      } catch (_) { /* fall through to bundled */ }
    }
    if (md == null) {
      const r = await fetch(`/web/docs/${encodeURIComponent(slug)}.md`);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      md = await r.text();
    }
    host.innerHTML = renderMarkdown(md);
  } catch (e) {
    host.innerHTML = `<p class="docs-placeholder">Could not load "${escHtml(slug)}": ${escHtml(e.message)}</p>`;
  }
}


// boot
setRoute(currentRouteName());
// Do one REST refresh so the page is populated even before the SSE
// connection is established (and as a backstop on browsers that fail to
// open EventSource). The stream takes over once the first event lands.
refresh().then(() => {
  if (currentRouteName() === "dashboard") { refreshDriftSparkline(); refreshBatteryHealth(); refreshRuntimeForecast(); }
  if (currentRouteName() === "history") { refreshChart(); refreshHeatmap(); }
});
openStream();
// Background charts still poll on their own slower cadence.
setInterval(() => {
  if (currentRouteName() === "history") refreshChart();
}, 30000);
