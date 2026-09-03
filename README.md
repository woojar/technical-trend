# technical-trend

A weekly technical news digest that collects what happened across Hacker News,
GitHub, arXiv and any RSS feed you point it at, deduplicates the same story
appearing in four places, ranks what mattered, and writes a Markdown issue.

Designed to run for **free**: the LLM layer is a fallback chain of free-tier
providers, and it degrades to a plain digest rather than failing when none of
them answer.

- Latest issue: [`digests/latest.md`](digests/latest.md)
- Archive: [`digests/`](digests/)

## How it works

```
sources ──> dedupe ──> rank ──> summarize ──> render ──> digests/YYYY-Www.md
                                    │                         └─> optional webhook
                                    └─ free LLM chain, or heuristic fallback
```

1. **Fetch.** Each source decides its own relevance window. A failing source is
   logged and skipped, never fatal.
2. **Dedupe.** Canonical URL match first, then title-token similarity. This is
   the step that decides whether the digest reads like a newsletter or like spam.
3. **Rank.** Attention is normalized *within* each source before comparison, so
   GitHub star counts do not automatically outrank Hacker News points.
4. **Summarize.** Items go to the LLM in batches, not one request each, which is
   what keeps a run inside free-tier daily limits.
5. **Render.** Markdown, grouped by category, every claim linked to its source.

## Setup

Requires [uv](https://docs.astral.sh/uv/). No other Python tooling needed.

```bash
uv sync
cp .env.example .env      # then add at least one API key
uv run trend providers    # confirm what is configured
uv run trend weekly       # write this week's digest
```

### Getting a free LLM key

You need **one**. The chain is tried in order and unconfigured entries are
skipped, so extra keys just add resilience.

| Provider | Free tier | Get a key |
|---|---|---|
| **Google Gemini** (recommended) | Generous daily quota, no card | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **Groq** | Generous, very fast | [console.groq.com/keys](https://console.groq.com/keys) |
| **OpenRouter** | Free model slugs (`:free` suffix) | [openrouter.ai/keys](https://openrouter.ai/keys) |
| **Cerebras** | Free tier | [cloud.cerebras.ai](https://cloud.cerebras.ai) |
| **Ollama** | Unlimited, fully local | `ollama pull qwen2.5:7b-instruct` |

Gemini Flash is the default first choice: this task is summarization and
classification, not hard reasoning, so a fast small model is the right tool. A
local 7B model through Ollama handles it acceptably too.

Free tiers change often. If a model slug stops working, `trend providers --probe`
will tell you which entry in the chain is broken.

## Usage

```bash
uv run trend weekly              # fetch, summarize, write digests/YYYY-Www.md
uv run trend weekly --dry-run    # print the outline, write nothing
uv run trend weekly --notify     # also post to configured webhooks
uv run trend fetch -n 40         # rank only, no LLM calls — for tuning sources
uv run trend providers --probe   # send one test request down the chain
uv run trend site                # build the static site into site/
uv run trend -v weekly           # debug logging
```

`trend fetch` costs no LLM quota, so use it when adjusting thresholds and feeds.

## Configuration

Everything lives in [`config.yaml`](config.yaml); secrets stay in the
environment. The knobs that matter most:

| Key | Effect |
|---|---|
| `window_days` | How far back a "week" reaches. |
| `max_entries` | Hard cap on stories sent to the LLM — your main cost control. |
| `max_per_category` | Stops one hot topic from swamping the issue. |
| `dedupe_threshold` | Title similarity needed to merge. Lower merges more aggressively. |
| `skip_seen` | Suppresses stories already published in an earlier digest. |
| `llm.batch_size` | Items per request. Higher means fewer requests, longer prompts. |
| `sources.hackernews.min_points` | Raise to cut noise, lower for wider coverage. |

### Adding a source

Most additions are config-only — add an entry under `sources.rss.feeds`:

```yaml
sources:
  rss:
    feeds:
      - name: Your Blog
        url: https://example.com/feed.xml
```

If a site has no feed, [RSSHub](https://docs.rsshub.app) can usually generate
one. For a source needing real logic, implement the `Source` protocol in
`src/trend/sources/base.py` and register it in `src/trend/sources/__init__.py`.

## Automation

[`.github/workflows/weekly.yml`](.github/workflows/weekly.yml) runs every Monday
at 06:00 UTC, commits the new digest, and can be triggered manually from the
Actions tab.

To enable it: add your provider key under **Settings → Secrets and variables →
Actions**. `GITHUB_TOKEN` is provided automatically and only raises the GitHub
Search API rate limit.

The workflow caches `state.db` between runs so a story popular for ten days does
not appear in two consecutive issues. Losing that cache is harmless — you just
get some repeats once.

For Slack or Discord delivery, add `SLACK_WEBHOOK_URL` or `DISCORD_WEBHOOK_URL`
as secrets. Without them the `--notify` flag is a no-op.

## Publishing to GitHub Pages

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) turns the committed
digests into a static site and deploys it. It runs whenever something lands in
`digests/`, so publishing follows automatically from the weekly commit.

To enable it, set **Settings → Pages → Build and deployment → Source** to
**GitHub Actions**. Nothing else is required: deployment uses the workflow's OIDC
token, so there is no secret to add.

Build it locally to see what will be published:

```bash
uv sync --group docs
uv run trend site
python -m http.server -d site 8000
```

`site/` is gitignored — it is a build artifact, rebuilt on every deploy.

The generator reads the Markdown in `digests/` and writes HTML beside it; it
never modifies the digests. That is deliberate. Jekyll only processes pages that
carry YAML front matter, and GitHub's Markdown viewer renders front matter as a
table at the top of every file, so adding it would trade a readable archive for a
buildable one. Converting at deploy time keeps both, and means issues written
before the site existed are published too.

It also emits `feed.xml`, so the digest can be read in a feed reader. The feed
needs absolute URLs, which the workflow supplies from `configure-pages` rather
than hardcoding the site address.

Two things to know: a Pages site is public even though the workflow only reads
the repo, and the `markdown` dependency lives in an optional `docs` group so a
weekly digest run installs three packages rather than four.

## Degraded mode

If every provider is rate-limited or unreachable, the run still produces a digest
using upstream titles and excerpts, and the footer says so explicitly. A weekly
job that quietly stops publishing is worse than one that publishes something
plainer, and a digest that looks normal but was built without a model would be
worse still.

## Development

```bash
uv sync                     # includes dev dependencies
uv run pytest               # 182 tests, no network access
uv run ruff check .
uv run ruff format .
```

Tests are offline by design: source parsing is verified against recorded API
payloads and real feed XML, and the LLM layer against a scripted fake provider.
That keeps CI deterministic and means the suite costs no API quota.

The 14 site-generator tests need the Markdown converter, so they skip unless you
run `uv sync --group docs`. CI installs it.

## Layout

```
src/trend/
├── cli.py            argparse entry point
├── config.py         config.yaml + .env loading
├── models.py         Item, Cluster, Entry, Digest
├── dedupe.py         URL canonicalization, story clustering
├── rank.py           cross-source normalized scoring
├── summarize.py      batched LLM editorial layer + heuristic fallback
├── render.py         Markdown output
├── notify.py         optional Slack/Discord webhooks
├── pipeline.py       orchestration
├── store.py          SQLite record of published stories
├── textutil.py       HTML-to-text for upstream excerpts
├── site.py           static site + RSS for GitHub Pages
├── http.py           shared session with retries
├── llm/              provider chain (gemini, openai_compat, router)
└── sources/          hackernews, github, arxiv, rss
```

## Notes and limitations

- **GitHub trending is approximated.** There is no official trending API, so the
  Search API is used to find recently created repositories that already have
  stars. This finds new hot projects but misses established repos having a big
  week.
- **Dedupe is lexical, not semantic.** Token-set similarity on titles, no
  embeddings. It reliably catches reposts and near-identical headlines, and will
  miss two outlets describing the same event in completely different words.
  Predictability was preferred over recall for an unattended job.
- **arXiv has no engagement signal**, so papers are ranked on recency and source
  weight alone and rely on the `Research` category quota for representation.
- **Free tiers are unstable.** Model slugs get retired and quotas change without
  notice. The chain exists for exactly this reason.
