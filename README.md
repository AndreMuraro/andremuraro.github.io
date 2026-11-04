# andremuraro.github.io

Personal site of André Muraro: [andremuraro.github.io](https://andremuraro.github.io).

Hand-written HTML pages plus a small Markdown blog, built by one Python script and
deployed to GitHub Pages by GitHub Actions.

| Path | What it is |
| --- | --- |
| `index.html`, `agent-memory-platform.html` | Hand-written pages, copied as they are. `<!-- writing:nav -->` and `<!-- writing:latest -->` are filled by the build once a post is published. |
| `posts/*.md` | Published posts. Front matter: `title`, `date`, `summary`, optional `tags`. File name `YYYY-MM-DD-slug.md` → `/writing/slug/`. |
| `drafts/` | Work in progress. Ignored by git, so drafts never reach this public repository. |
| `templates/`, `assets/site.css` | Layout and styles for generated pages. |
| `build.py` | The build: posts, writing index, Atom feed, sitemap, code highlighting. Dependencies are declared inline and locked in `build.py.lock`. |
| `.github/workflows/pages.yml` | Builds on every push and pull request; deploys from `main`. |

## Writing a post

```sh
# write drafts/2026-11-10-my-post.md, then preview it locally
uv run build.py --drafts --out _preview
python -m http.server -d _preview   # http://localhost:8000/writing/my-post/

# publish: move it to posts/, remove `draft: true`, then commit and push
mv drafts/2026-11-10-my-post.md posts/
```

## Settings

- Settings > Pages > Source must be **GitHub Actions**.
- Custom domain later: add it in Settings > Pages; set `SITE_URL` in the workflow so the
  feed and canonical links use it.
