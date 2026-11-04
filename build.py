# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "markdown-it-py>=4.0",
#     "mdit-py-plugins>=0.5",
#     "pygments>=2.19",
#     "pyyaml>=6.0.2",
# ]
# ///
"""Build the site into ``_site/``.

Hand-written pages (``*.html`` at the root) are copied as they are, except for two
markers that the build fills once at least one post is published:

- ``<!-- writing:nav -->`` becomes a "Writing" link;
- ``<!-- writing:latest -->`` becomes a section listing the latest posts.

Posts are Markdown files in ``posts/`` with YAML front matter (``title``, ``date``,
``summary``, optional ``tags`` and ``draft``). Work in progress goes in ``drafts/``, which
git ignores, so it never reaches the public repository. Drafts (that folder, or
``draft: true``) are built only with ``--drafts``.

Usage::

    uv run build.py                 # what GitHub Pages serves
    uv run build.py --drafts        # include drafts, for a local preview
    python -m http.server -d _site  # then open http://localhost:8000
"""

from __future__ import annotations

import argparse
import html
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from string import Template
from xml.sax.saxutils import escape as xml_escape

import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.footnote import footnote_plugin
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

ROOT = Path(__file__).parent
SITE_URL = os.environ.get("SITE_URL", "https://andremuraro.github.io").rstrip("/")
AUTHOR = "André Muraro"
WORDS_PER_MINUTE = 220
LATEST_ON_HOME = 3
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
DATED_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-")


@dataclass(frozen=True, slots=True)
class Post:
    slug: str
    title: str
    published: date
    summary: str
    tags: tuple[str, ...]
    draft: bool
    body_html: str
    minutes: int

    @property
    def path(self) -> str:
        return f"/writing/{self.slug}/"

    @property
    def url(self) -> str:
        return SITE_URL + self.path

    @property
    def human_date(self) -> str:
        return f"{self.published.day} {self.published:%b %Y}"


def _highlight(code: str, lang: str, _attrs: str) -> str:
    """Pygments token spans; markdown-it wraps them in ``<pre><code>``."""
    if not lang:
        return ""
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        return ""
    return pygments_highlight(code, lexer, HtmlFormatter(nowrap=True))


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"highlight": _highlight, "typographer": True})
    md.enable(["table", "strikethrough", "replacements", "smartquotes"])
    md.use(footnote_plugin)
    md.use(anchors_plugin, min_level=2, max_level=3, permalink=True, permalinkSymbol="#")
    return md


def load_post(path: Path, md: MarkdownIt) -> Post:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER.match(text)
    if match is None:
        msg = f"{path.name}: missing front matter"
        raise SystemExit(msg)
    meta = yaml.safe_load(match.group(1)) or {}
    missing = {"title", "date", "summary"} - meta.keys()
    if missing:
        msg = f"{path.name}: front matter lacks {', '.join(sorted(missing))}"
        raise SystemExit(msg)
    body = text[match.end() :]
    words = len(re.findall(r"\w+", body))
    published = meta["date"]
    if not isinstance(published, date):
        published = date.fromisoformat(str(published))
    return Post(
        slug=DATED_NAME.sub("", path.stem),
        title=str(meta["title"]),
        published=published,
        summary=str(meta["summary"]).strip(),
        tags=tuple(str(t) for t in meta.get("tags", [])),
        draft=bool(meta.get("draft", False)) or path.parent.name == "drafts",
        body_html=md.render(body),
        minutes=max(1, math.ceil(words / WORDS_PER_MINUTE)),
    )


def load_posts(*, include_drafts: bool) -> list[Post]:
    """Load ``posts/``, plus the git-ignored ``drafts/`` when drafts are included."""
    md = _markdown()
    sources = sorted((ROOT / "posts").glob("*.md"))
    if include_drafts:
        sources += sorted((ROOT / "drafts").glob("*.md"))
    posts = [load_post(p, md) for p in sources]
    slugs = [p.slug for p in posts]
    if len(slugs) != len(set(slugs)):
        msg = "two posts share a slug"
        raise SystemExit(msg)
    visible = [p for p in posts if include_drafts or not p.draft]
    return sorted(visible, key=lambda p: (p.published, p.slug), reverse=True)


_DRAFT_BADGE = ' <span class="status">draft</span>'


def _template(name: str) -> Template:
    return Template((ROOT / "templates" / name).read_text(encoding="utf-8"))


def _post_list(posts: list[Post]) -> str:
    items = "\n".join(
        f"""  <li>
    <time datetime="{p.published.isoformat()}">{p.human_date}</time>
    <div>
      <h3><a href="{p.path}">{html.escape(p.title)}</a>{_DRAFT_BADGE if p.draft else ""}</h3>
      <p>{html.escape(p.summary)}</p>
    </div>
  </li>"""
        for p in posts
    )
    return f'<ol class="posts" reversed>\n{items}\n</ol>'


def render_post(post: Post) -> str:
    tags = " · ".join(html.escape(t) for t in post.tags)
    return _template("post.html").substitute(
        title=html.escape(post.title),
        summary=html.escape(post.summary),
        url=post.url,
        iso_date=post.published.isoformat(),
        human_date=post.human_date,
        minutes=post.minutes,
        tags=f'<p class="tags">{tags}</p>' if tags else "",
        body=post.body_html,
        robots='<meta name="robots" content="noindex">' if post.draft else "",
    )


def render_writing_index(posts: list[Post]) -> str:
    listing = _post_list(posts) if posts else '<p class="empty">No posts yet.</p>'
    return _template("writing.html").substitute(posts=listing, url=f"{SITE_URL}/writing/")


def render_feed(posts: list[Post]) -> str:
    updated = datetime.combine(posts[0].published, time(12), UTC) if posts else datetime.now(UTC)
    entries = "\n".join(
        f"""  <entry>
    <title>{xml_escape(p.title)}</title>
    <link href="{p.url}"/>
    <id>{p.url}</id>
    <published>{datetime.combine(p.published, time(12), UTC).isoformat()}</published>
    <updated>{datetime.combine(p.published, time(12), UTC).isoformat()}</updated>
    <summary>{xml_escape(p.summary)}</summary>
    <content type="html">{xml_escape(p.body_html)}</content>
  </entry>"""
        for p in posts
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{AUTHOR} · Writing</title>
  <link href="{SITE_URL}/writing/"/>
  <link rel="self" href="{SITE_URL}/feed.xml"/>
  <id>{SITE_URL}/</id>
  <updated>{updated.isoformat()}</updated>
  <author><name>{AUTHOR}</name></author>
{entries}
</feed>
"""


def render_sitemap(pages: list[str]) -> str:
    urls = "\n".join(f"  <url><loc>{SITE_URL}{p}</loc></url>" for p in pages)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n"
    )


def code_css() -> str:
    light = HtmlFormatter(style="friendly").get_style_defs("pre code")
    dark = HtmlFormatter(style="github-dark").get_style_defs([':root[data-theme="dark"] pre code'])
    dark_auto = HtmlFormatter(style="github-dark").get_style_defs(
        [':root:not([data-theme="light"]) pre code']
    )
    # Token colours only: the block's background comes from site.css.
    strip = re.compile(r"^.*\{ background: [^}]*\}\n?|^.*\.hll \{[^}]*\}\n?", re.MULTILINE)
    return (
        "/* Generated by build.py from Pygments styles. */\n"
        + strip.sub("", light)
        + "\n"
        + strip.sub("", dark)
        + "\n@media (prefers-color-scheme: dark) {\n"
        + strip.sub("", dark_auto)
        + "\n}\n"
    )


def fill_markers(page: str, posts: list[Post]) -> str:
    if not posts:
        return page
    latest = f"""<section id="writing" class="stack" aria-labelledby="writing-h">
  <h2 id="writing-h">Writing</h2>
  {_post_list(posts[:LATEST_ON_HOME])}
  <p class="mono"><a href="/writing/">All writing →</a></p>
</section>"""
    return page.replace("<!-- writing:nav -->", '<a href="/writing/">Writing</a>').replace(
        "<!-- writing:latest -->", latest
    )


def build(out: Path, *, include_drafts: bool) -> list[Post]:
    posts = load_posts(include_drafts=include_drafts)
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / ".nojekyll").touch()

    pages = ["/"]
    for page in sorted(ROOT.glob("*.html")):
        (out / page.name).write_text(
            fill_markers(page.read_text(encoding="utf-8"), posts), encoding="utf-8"
        )
        if page.name not in {"index.html", "404.html"}:
            pages.append(f"/{page.name}")

    for asset in (ROOT / "assets").iterdir():
        shutil.copy2(asset, out / "assets" / asset.name)
    (out / "assets" / "code.css").write_text(code_css(), encoding="utf-8")

    (out / "writing").mkdir()
    (out / "writing" / "index.html").write_text(render_writing_index(posts), encoding="utf-8")
    pages.append("/writing/")
    for post in posts:
        target = out / "writing" / post.slug
        target.mkdir()
        (target / "index.html").write_text(render_post(post), encoding="utf-8")
        if not post.draft:
            pages.append(post.path)

    published = [p for p in posts if not p.draft]
    (out / "feed.xml").write_text(render_feed(published), encoding="utf-8")
    (out / "sitemap.xml").write_text(render_sitemap(pages), encoding="utf-8")
    return posts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--drafts", action="store_true", help="include draft posts")
    parser.add_argument("--out", type=Path, default=ROOT / "_site", help="output folder")
    args = parser.parse_args()
    posts = build(args.out, include_drafts=args.drafts)
    drafts = sum(p.draft for p in posts)
    print(f"built {args.out} with {len(posts) - drafts} posts and {drafts} drafts")


if __name__ == "__main__":
    main()
