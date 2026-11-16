#!/usr/bin/env python3
"""Build https://wiroai.github.io from the public repositories of the wiroai organization.

Reads every public repository with the GitHub CLI (`gh`), renders each README with the
GitHub Markdown API, and writes a static site: the index page, one page per project under
projects/<name>/, sitemap.xml, robots.txt and 404.html.

Usage: python3 build.py   (needs `gh` signed in; standard library only)
"""
import datetime
import html
import json
import os
import re
import subprocess
import tempfile

ORG = "wiroai"
SITE = "https://wiroai.github.io"
ROOT = os.path.dirname(os.path.abspath(__file__))
OG_IMAGE = "https://wiro.ai/images/og/default.jpg"

# Repositories in display order, grouped. A public repo missing here lands in "More projects".
GROUPS = [
    ("assistants", "Coding assistants and agents",
     "Use Wiro AI models from Cursor, Claude Code, Codex, Gemini CLI, Zed, Hermes Agent and GitHub Actions.",
     ["Wiro-MCP", "Wiro-Plugin", "Wiro-Skills", "Wiro-Zed", "Wiro-Hermes", "Wiro-Action"]),
    ("sdks", "LLM gateway and SDKs",
     "Clients for the Wiro AI API and for the OpenAI and Anthropic compatible LLM gateway.",
     ["Wiro-LLM", "Swift-QuickStart", "Kotlin-QuickStart", "Dart-QuickStart", "ReactNative-QuickStart"]),
    ("realtime", "Realtime voice examples",
     "Example apps for live voice conversation, speech-to-text and text-to-speech on iOS and Android.",
     ["Swift-Realtime", "Kotlin-Realtime", "Dart-Realtime", "ReactNative-Realtime"]),
    ("creative", "Creative and design tools",
     "Run Wiro AI models inside ComfyUI, Blender and Figma.",
     ["Wiro-ComfyUI", "Wiro-Blender", "Wiro-Figma"]),
    ("automation", "Automation and no-code",
     "Run models from n8n workflows, Dify apps and the WordPress editor.",
     ["n8n-nodes-wiro", "Wiro-Dify-Plugin", "Wiro-WordPress"]),
    ("apps", "Productivity apps",
     "Wiro AI in your launcher and your notes.",
     ["Wiro-Raycast", "Wiro-Obsidian"]),
    ("browser", "Browser extensions",
     "Right-click any image, video, audio clip, text or page to run a model.",
     ["Wiro-Chromium-Extension", "Wiro-Firefox-Extension", "Wiro-Safari-Extension", "Wiro-Extension-Core"]),
    ("templates", "Templates and docs",
     "A Next.js starter app and the source of the Wiro AI API docs.",
     ["Wiro-Next-Starter", "Wiro-Docs"]),
]
SKIP = {f"{ORG}.github.io"}

# Live store listings. Only add a store once its public page is live.
CWS = "https://chromewebstore.google.com/detail/wiro-ai-tools-for-images/cehkhdmppbnfeahegjjkdhangjelehab"
AMO = "https://addons.mozilla.org/firefox/addon/wiro-ai/"
WHALE = "https://store.whale.naver.com/detail/elgobhnkemhlhfakdmpijbknhehnkgle"
STORES = {
    "Wiro-Chromium-Extension": [("Install Chrome Extension", CWS), ("Install on Naver Whale", WHALE)],
    "Wiro-Firefox-Extension": [("Install Firefox Add-on", AMO)],
}
GROUP_LINKS = {
    "browser": [("Install Chrome Extension", CWS), ("Install Firefox Add-on", AMO), ("Install on Naver Whale", WHALE)],
}

NAV = [
    ("Models", "https://wiro.ai/models"),
    ("Agents", "https://wiro.ai/agents"),
    ("Docs", "https://wiro.ai/docs"),
    ("Pricing", "https://wiro.ai/product/pricing"),
    ("GitHub", f"https://github.com/{ORG}"),
]
FOOTER_LINKS = [
    ("Wiro AI", "https://wiro.ai"),
    ("Models", "https://wiro.ai/models"),
    ("Workflows", "https://wiro.ai/workflows"),
    ("Agents", "https://wiro.ai/agents"),
    ("MCP server", "https://wiro.ai/docs/wiro-mcp-server"),
    ("LLM gateway", "https://wiro.ai/llm"),
    ("Browser extension", "https://wiro.ai/browser-extension"),
    ("Integrations", "https://wiro.ai/integrations"),
    ("Pricing", "https://wiro.ai/product/pricing"),
    ("Docs", "https://wiro.ai/docs"),
    ("Blog", "https://wiro.ai/blog"),
    ("X", "https://x.com/wiroai"),
    ("LinkedIn", "https://www.linkedin.com/company/wiroai"),
    ("GitHub", f"https://github.com/{ORG}"),
]


def gh(args, input_file=None):
    cmd = ["gh", "api"] + args
    if input_file:
        cmd += ["--input", input_file]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def esc(s):
    return html.escape(s or "", quote=True)


def slug(name):
    return name.lower()


def heading_slug(text, seen):
    base = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


def fix_readme_html(raw, repo, branch):
    blob = f"https://github.com/{ORG}/{repo}/blob/{branch}/"
    rawbase = f"https://raw.githubusercontent.com/{ORG}/{repo}/{branch}/"

    def absolute(url, base):
        if re.match(r"^(https?:|mailto:|#|//|data:)", url):
            return url
        return base + re.sub(r"^\./", "", url)

    out = re.sub(r'href="([^"]*)"', lambda m: f'href="{absolute(m.group(1), blob)}"', raw)
    out = re.sub(r'src="([^"]*)"', lambda m: f'src="{absolute(m.group(1), rawbase)}"', out)
    # Our own content: drop nofollow from every rel attribute.
    def clean_rel(m):
        tokens = [t for t in m.group(1).split() if t != "nofollow"]
        return f' rel="{" ".join(tokens)}"' if tokens else ""

    out = re.sub(r' rel="([^"]*)"', clean_rel, out)
    # The page already has its own <h1>; README titles become <h2>.
    out = re.sub(r"<(/?)h1(\b[^>]*)>", r"<\1h2\2>", out)
    # Lazy-load every image except the first one (the README logo).
    first, _, rest = out.partition("<img ")
    out = first + ("<img " + rest.replace("<img ", '<img loading="lazy" ') if _ else "")
    # Give headings GitHub-style ids so in-page links like #quick-start work.
    seen = {}

    def add_id(m):
        level, attrs, inner = m.group(1), m.group(2), m.group(3)
        text = re.sub(r"<[^>]+>", "", inner)
        return f'<h{level}{attrs} id="{heading_slug(html.unescape(text), seen)}">{inner}</h{level}>'

    out = re.sub(r"<h([1-6])([^>]*)>(.*?)</h\1>", add_id, out, flags=re.S)
    return out


def page(title, description, canonical, body, jsonld):
    nav = "".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in NAV)
    foot = "".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in FOOTER_LINKS)
    year = datetime.date.today().year
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(canonical)}">
<link rel="icon" type="image/png" href="/assets/wiro-icon.png">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Wiro AI Open Source">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:image" content="{OG_IMAGE}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@wiroai">
<link rel="stylesheet" href="/assets/site.css">
<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>
</head>
<body>
<header class="top">
  <div class="wrap top-in">
    <a class="brand" href="/"><img src="/assets/wiro-icon.png" alt="" width="32" height="32"><span>Wiro AI <em>Open Source</em></span></a>
    <nav>{nav}</nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="foot">
  <div class="wrap">
    <p class="foot-links">{foot}</p>
    <p>&copy; {year} Wiro AI Inc. &middot; San Francisco, CA &middot; <a href="mailto:support@wiro.ai">support@wiro.ai</a></p>
  </div>
</footer>
</body>
</html>
"""


def card(r):
    links = [f'<a class="install" href="{esc(u)}">{esc(t)}</a>' for t, u in STORES.get(r["name"], [])]
    links += [f'<a href="/projects/{slug(r["name"])}/">Project page</a>',
              f'<a href="{esc(r["html_url"])}">GitHub</a>']
    home = r.get("homepage") or ""
    if home and home.rstrip("/") != "https://wiro.ai":
        links.append(f'<a href="{esc(home)}">Docs</a>')
    tags = "".join(f"<li>{esc(t)}</li>" for t in (r.get("topics") or [])[:5])
    meta = " &middot; ".join(x for x in [esc(r.get("language") or ""), esc((r.get("license") or {}).get("spdx_id") or "")] if x and x != "NOASSERTION")
    return f"""<article class="card">
  <h3><a href="/projects/{slug(r["name"])}/">{esc(r["name"])}</a></h3>
  <p>{esc(r.get("description") or "")}</p>
  {f'<p class="meta">{meta}</p>' if meta else ''}
  <ul class="tags">{tags}</ul>
  <p class="links">{" ".join(links)}</p>
</article>"""


def main():
    repos = json.loads(gh([f"orgs/{ORG}/repos?per_page=100&type=public"]))
    repos = {r["name"]: r for r in repos if not r["private"] and not r["archived"] and r["name"] not in SKIP}
    grouped = [(gid, title, intro, [repos[n] for n in names if n in repos]) for gid, title, intro, names in GROUPS]
    listed = {r["name"] for _, _, _, rs in grouped for r in rs}
    rest = sorted((r for n, r in repos.items() if n not in listed), key=lambda r: r["name"].lower())
    if rest:
        grouped.append(("more", "More projects", "Other public repositories from Wiro AI.", rest))
    today = datetime.date.today().isoformat()
    urls = [(f"{SITE}/", today)]

    # Index page.
    toc = "".join(f'<a href="#{gid}">{esc(title)}</a>' for gid, title, _, rs in grouped if rs)
    sections = []
    for gid, title, intro, rs in grouped:
        if not rs:
            continue
        group_cta = "".join(f'<a class="btn" href="{esc(u)}">{esc(t)}</a> ' for t, u in GROUP_LINKS.get(gid, []))
        sections.append(f"""<section id="{gid}" class="group">
  <h2>{esc(title)}</h2>
  <p class="intro">{esc(intro)}</p>
  {f'<p class="cta">{group_cta.strip()}</p>' if group_cta else ''}
  <div class="grid">{"".join(card(r) for r in rs)}</div>
</section>""")
    body = f"""<section class="hero">
  <h1>Wiro AI open source</h1>
  <p class="lead">Plugins, SDKs, browser extensions, agent tools and example apps for <a href="https://wiro.ai">Wiro AI</a>. Each one talks to the same API, with <a href="https://wiro.ai/models">1,000+ image, video, audio, 3D and LLM models</a>.</p>
  <p class="cta"><a class="btn" href="https://wiro.ai">Visit wiro.ai</a> <a class="btn ghost" href="https://wiro.ai/docs">Read the API docs</a> <a class="btn ghost" href="https://github.com/{ORG}">Follow on GitHub</a></p>
  <p class="count">{len(repos)} public repositories</p>
  <nav class="toc">{toc}</nav>
</section>
{"".join(sections)}"""
    jsonld = {"@context": "https://schema.org", "@type": "CollectionPage", "name": "Wiro AI Open Source",
              "url": f"{SITE}/", "description": "Open-source plugins, SDKs, browser extensions and agent tools for Wiro AI.",
              "publisher": {"@type": "Organization", "name": "Wiro AI", "url": "https://wiro.ai",
                            "logo": "https://wiro.ai/images/favicon/icon-512x512.png",
                            "sameAs": ["https://x.com/wiroai", "https://www.linkedin.com/company/wiroai", f"https://github.com/{ORG}"]}}
    with open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page("Wiro AI Open Source: plugins, SDKs and extensions",
                     f"{len(repos)} open-source projects for Wiro AI: MCP server, coding assistant plugins, agent skills, SDKs, ComfyUI, Blender, Figma, n8n and browser extensions for 1,000+ AI models.",
                     f"{SITE}/", body, jsonld))

    # Project pages.
    for gid, title, intro, rs in grouped:
        for r in rs:
            name, branch = r["name"], r.get("default_branch") or "main"
            try:
                md = subprocess.run(["gh", "api", f"repos/{ORG}/{name}/readme", "-H", "Accept: application/vnd.github.raw"],
                                    check=True, capture_output=True, text=True).stdout
            except subprocess.CalledProcessError:
                md = r.get("description") or ""
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
                json.dump({"text": md, "mode": "gfm", "context": f"{ORG}/{name}"}, tf)
                payload = tf.name
            try:
                readme = fix_readme_html(gh(["-X", "POST", "markdown"], input_file=payload), name, branch)
            finally:
                os.unlink(payload)
            siblings = [x for x in rs if x["name"] != name] + [x for _, _, _, xs in grouped for x in xs if x not in rs]
            more = "".join(f'<li><a href="/projects/{slug(x["name"])}/">{esc(x["name"])}</a></li>' for x in siblings[:12])
            stores = STORES.get(name, [])
            buttons = [f'<a class="btn" href="{esc(u)}">{esc(t)}</a>' for t, u in stores]
            buttons.append(f'<a class="btn{" ghost" if stores else ""}" href="{esc(r["html_url"])}">View on GitHub</a>')
            home = r.get("homepage") or ""
            if home:
                label = "Website" if home.rstrip("/") == "https://wiro.ai" else "Docs"
                buttons.append(f'<a class="btn ghost" href="{esc(home)}">{label}</a>')
            body = f"""<p class="crumbs"><a href="/">Wiro AI open source</a> / <a href="/#{gid}">{esc(title)}</a></p>
<section class="project-head">
  <h1>{esc(name)}</h1>
  <p class="lead">{esc(r.get("description") or "")}</p>
  <p class="cta">{" ".join(buttons)}</p>
</section>
<article class="markdown">
{readme}
</article>
<aside class="more">
  <h2>More open-source projects from Wiro AI</h2>
  <ul>{more}</ul>
  <p><a href="/">See all {len(repos)} projects</a></p>
</aside>"""
            canonical = f"{SITE}/projects/{slug(name)}/"
            jsonld = {"@context": "https://schema.org", "@type": "SoftwareSourceCode", "name": name,
                      "description": r.get("description") or "", "codeRepository": r["html_url"], "url": canonical,
                      "programmingLanguage": r.get("language") or None,
                      "license": ((r.get("license") or {}).get("url")) or None,
                      "author": {"@type": "Organization", "name": "Wiro AI", "url": "https://wiro.ai"}}
            jsonld = {k: v for k, v in jsonld.items() if v is not None}
            out_dir = os.path.join(ROOT, "projects", slug(name))
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(page(f"{name}: Wiro AI open source", r.get("description") or name, canonical, body, jsonld))
            urls.append((canonical, (r.get("pushed_at") or today)[:10]))

    # 404, sitemap, robots.
    with open(os.path.join(ROOT, "404.html"), "w", encoding="utf-8") as f:
        f.write(page("Page not found: Wiro AI Open Source", "This page does not exist.", f"{SITE}/",
                     '<section class="hero"><h1>Page not found</h1><p class="lead">This page does not exist. '
                     '<a href="/">See all Wiro AI open-source projects</a> or visit <a href="https://wiro.ai">wiro.ai</a>.</p></section>',
                     {"@context": "https://schema.org", "@type": "WebPage", "name": "Page not found"}))
    with open(os.path.join(ROOT, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for loc, mod in urls:
            f.write(f"  <url><loc>{loc}</loc><lastmod>{mod}</lastmod></url>\n")
        f.write("</urlset>\n")
    with open(os.path.join(ROOT, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n")
    print(f"built {len(urls)} pages")


if __name__ == "__main__":
    main()
