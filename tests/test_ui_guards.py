"""Guard rails for the appliance UI, so the UX remediation does not quietly rot.

Static checks only (no browser): token contrast in both themes, the type scale,
budgets for literal colours and `!important`, dead CSS classes, labelled form
controls and heading order. `scripts/ux/audit.mjs` is the browser-side
counterpart (computed contrast, tap targets, overflow) and is run by hand.

The budgets are ceilings: lower them when you pay debt down, never raise them
to make a change fit.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "solar_monitor" / "web"
CSS_FILES = sorted((WEB / "css").glob("*.css"))

# Ceilings, measured when the guard was added.
MAX_IMPORTANT = 12
MAX_LITERAL_COLOURS_OUTSIDE_BASE = 219
MAX_INLINE_STYLES_IN_INDEX = 50


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _all_css() -> str:
    return _strip_comments("".join(p.read_text() for p in CSS_FILES))


# --- colour maths -----------------------------------------------------------

def _rgb(value: str):
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    m = re.fullmatch(r"#([0-9a-fA-F]{3})", value)
    if m:
        return tuple(int(c * 2, 16) / 255 for c in m.group(1))
    m = re.fullmatch(r"hsl\(\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\s*\)", value)
    if m:
        h, s, l = float(m.group(1)) / 360, float(m.group(2)) / 100, float(m.group(3)) / 100
        c = (1 - abs(2 * l - 1)) * s
        x = c * (1 - abs((h * 6) % 2 - 1))
        mm = l - c / 2
        sector = int(h * 6) % 6
        r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][sector]
        return (r + mm, g + mm, b + mm)
    raise ValueError(f"unsupported colour {value!r}")


def _lum(rgb) -> float:
    def ch(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _lum(_rgb(a)), _lum(_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _theme_tokens():
    base = _strip_comments((WEB / "css" / "01-base.css").read_text())
    dark_block = re.search(r":root\s*\{(.*?)\n\}", base, flags=re.S).group(1)
    light_block = re.search(r'\[data-theme="light"\]\s*\{(.*?)\n\}', base, flags=re.S).group(1)

    def parse(block):
        return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)}

    dark = parse(dark_block)
    light = {**dark, **parse(light_block)}
    return {"dark": dark, "light": light}


def test_text_tokens_meet_aa_on_every_surface_in_both_themes():
    failures = []
    for theme, tokens in _theme_tokens().items():
        for text in ("--text", "--text-2", "--text-3"):
            for surface in ("--bg", "--surface", "--surface-2"):
                ratio = _contrast(tokens[text], tokens[surface])
                if ratio < 4.5:
                    failures.append(f"{theme}: {text} on {surface} = {ratio:.2f}")
        ratio = _contrast(tokens["--on-accent"], tokens["--accent"])
        if ratio < 4.5:
            failures.append(f"{theme}: --on-accent on --accent = {ratio:.2f}")
    assert not failures, failures


# --- scales and budgets -----------------------------------------------------

def test_font_sizes_come_from_the_scale():
    css = _all_css()
    allowed_prefixes = ("var(--fs-", "clamp(", "inherit", "em", "%")
    stray = []
    for raw in re.findall(r"font-size:\s*([^;}!]+)", css):
        v = raw.strip()
        if v.startswith(allowed_prefixes[:3]) or v.endswith("em") and not v.endswith("rem"):
            continue
        m = re.fullmatch(r"([\d.]+)(rem|px)", v)
        if m and (float(m.group(1)) >= 1.6 and m.group(2) == "rem" or m.group(2) == "px" and float(m.group(1)) >= 26):
            continue  # display numerals keep their own sizes
        stray.append(v)
    assert not stray, f"font-size must use a --fs-* token (or be a display size >= 1.6rem): {sorted(set(stray))}"


def test_no_text_smaller_than_the_scale_minimum():
    tokens = _theme_tokens()["dark"]
    smallest = min(float(re.fullmatch(r"([\d.]+)rem", tokens[k]).group(1)) for k in tokens if k.startswith("--fs-"))
    assert smallest * 16 >= 11, "smallest type token must stay at or above 11px"


def test_important_budget():
    count = len(re.findall(r"!important", _all_css()))
    assert count <= MAX_IMPORTANT, f"{count} !important declarations (ceiling {MAX_IMPORTANT})"


def test_literal_colour_budget_outside_the_token_file():
    css = _strip_comments("".join(p.read_text() for p in CSS_FILES if p.name != "01-base.css"))
    count = len(re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", css))
    assert count <= MAX_LITERAL_COLOURS_OUTSIDE_BASE, (
        f"{count} literal colours outside 01-base.css (ceiling {MAX_LITERAL_COLOURS_OUTSIDE_BASE}); use a token"
    )


def test_inline_style_budget_in_index():
    count = len(re.findall(r'\sstyle="', (WEB / "index.html").read_text()))
    assert count <= MAX_INLINE_STYLES_IN_INDEX, f"{count} inline styles in index.html (ceiling {MAX_INLINE_STYLES_IN_INDEX})"


def test_every_css_class_is_used_somewhere():
    css = _all_css()
    css = re.sub(r"url\([^)]*\)|\"[^\"]*\"|'[^']*'|\d+\.\d+", "", css)
    classes = set(re.findall(r"\.([a-zA-Z_][\w-]*)", css))
    def code_only(src: str, suffix: str) -> str:
        if suffix == ".js":
            src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
            src = re.sub(r"(?<![:\"'\\])//[^\n]*", "", src)
        elif suffix == ".html":
            src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
        return src
    corpus = "".join(
        code_only(p.read_text(), p.suffix)
        for p in WEB.rglob("*")
        if p.suffix in {".js", ".html"} and "vendor" not in p.parts and p.name != "uPlot.iife.min.js"
    )
    corpus += "".join(p.read_text() for p in (ROOT / "solar_monitor").rglob("*.py"))
    # Built at runtime from data (`kind-${kind}`, `acc-${cls}`, ...) or owned by a vendored library.
    dynamic = ("kind-", "acc-", "ar-tag--", "u-", "uplot", "reverse")
    dead = sorted(
        c for c in classes
        if not re.search(r"(?<![\w-])" + re.escape(c) + r"(?![\w-])", corpus) and not c.startswith(dynamic)
    )
    assert not dead, f"CSS classes nothing refers to: {dead}"


# --- markup -----------------------------------------------------------------

class _Doc(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = []
        self.label_for = set()
        self.label_depth = 0
        self.headings = []
        self.h1_classes = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        if tag in {"input", "select", "textarea"} and a.get("type") not in {"hidden", "submit", "button"}:
            self.controls.append((tag, a, self.label_depth > 0))
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.headings.append(int(tag[1]))
            if tag == "h1":
                self.h1_classes.append(a.get("class", ""))

    def handle_endtag(self, tag):
        if tag == "label" and self.label_depth:
            self.label_depth -= 1


def _doc(name="index.html") -> _Doc:
    d = _Doc()
    d.feed((WEB / name).read_text())
    return d


def test_every_form_control_has_an_accessible_name():
    d = _doc()
    unnamed = []
    for tag, a, inside_label in d.controls:
        named = (
            a.get("aria-label") or a.get("aria-labelledby") or a.get("title")
            or inside_label or (a.get("id") and a["id"] in d.label_for)
        )
        if not named:
            unnamed.append(f"<{tag} id={a.get('id')!r} name={a.get('name')!r}>")
    assert not unnamed, f"controls with no accessible name: {unnamed}"


def test_heading_levels_never_skip_and_each_main_view_has_an_h1():
    d = _doc()
    prev = 0
    skips = []
    for level in d.headings:
        if level > prev + 1 and prev != 0:
            skips.append((prev, level))
        prev = level
    assert not skips, f"heading level skips in index.html: {skips}"
    sr_only = [c for c in d.h1_classes if "sr-only" in c]
    assert len(sr_only) >= 3, "Dashboard, History and Devices each need a (visually hidden) h1"
