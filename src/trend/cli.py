"""Command line interface.

trend weekly      generate and write this week's digest
trend fetch       fetch and rank only, print what would be included
trend providers   show which LLM providers are configured and reachable
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from trend import __version__
from trend.config import load_config
from trend.dedupe import cluster_items
from trend.http import build_llm_session
from trend.llm.base import LLMError
from trend.llm.router import Router
from trend.notify import notify
from trend.pipeline import build_digest, fetch_items, write_digest
from trend.rank import score_clusters
from trend.render import source_label
from trend.sources import source_weights

log = logging.getLogger("trend")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # These libraries are chatty at DEBUG and drown out our own output.
    for noisy in ("urllib3", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_weekly(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, root=args.root)
    digest = build_digest(cfg, dry_run=args.dry_run)

    if not digest.entries:
        log.error("no stories found; check source configuration and network access")
        return 1

    if args.dry_run:
        print(
            f"[dry run] {digest.week}: {len(digest.entries)} entries, "
            f"provider={digest.provider or 'heuristic fallback'}"
        )
        for i, entry in enumerate(digest.entries, 1):
            print(f"{i:3d}. [{entry.category}] {entry.title}")
        return 0

    path = write_digest(digest, cfg)
    print(f"Wrote {path} ({len(digest.entries)} stories)")

    if not digest.provider:
        log.warning("digest generated without an LLM; set GEMINI_API_KEY or another provider key")

    if args.notify:
        delivered = notify(digest)
        if delivered:
            print(f"Notified: {', '.join(delivered)}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Rank without spending any LLM quota -- useful for tuning sources."""
    cfg = load_config(args.config, root=args.root)
    now = datetime.now(UTC)
    result = fetch_items(cfg, now=now)

    if not result.items:
        log.error("no items fetched; check network access and source configuration")
        return 1

    clusters = cluster_items(result.items, threshold=cfg.dedupe_threshold)
    score_clusters(clusters, source_weights(), window_days=cfg.window_days, now=now)

    print(f"Window: {result.window_start:%Y-%m-%d} to {result.window_end:%Y-%m-%d}")
    for name, count in sorted(result.per_source.items()):
        print(f"  {name:<12} {count:4d} items")
    print(f"  {'total':<12} {len(result.items):4d} items -> {len(clusters)} unique stories")
    print()

    for i, cluster in enumerate(clusters[: args.limit], 1):
        primary = cluster.primary
        tag = "+".join(source_label(s) for s in cluster.sources)
        print(f"{i:3d}. [{cluster.score:.3f}] ({tag}) {primary.title[:95]}")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    cfg = load_config(args.config, root=args.root)
    router = Router.from_config(cfg.llm_chain, session=build_llm_session())

    if not router.providers:
        print("No providers configured. Check the llm.chain block in config.yaml.")
        return 1

    print("Provider chain (first configured provider wins, rest are fallbacks):")
    for name, model, configured in router.describe():
        # "ready" means credentials are present, not that the endpoint answers.
        # Keyless entries such as a local Ollama are always ready; use --probe
        # to find out whether anything actually responds.
        print(f"  {'ready ' if configured else 'no key'}  {name:<12} {model}")

    if not router.has_available:
        print("\nNo provider has credentials. Set one of the keys listed in .env.example.")
        print("The pipeline will still run, producing headlines and excerpts only.")
        return 1

    if args.probe:
        print("\nProbing live...")
        try:
            completion = router.complete(
                "Reply with exactly the word: ok", "Reply with exactly the word: ok"
            )
        except LLMError as exc:
            print(f"  FAILED: {exc}")
            return 1
        print(f"  {completion.provider} ({completion.model}) responded: {completion.text[:60]!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trend",
        description="Weekly technical trend digest assistant.",
    )
    parser.add_argument("--version", action="version", version=f"trend {__version__}")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config file")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="project root for relative paths (default: current directory)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    sub = parser.add_subparsers(dest="command", required=True)

    weekly = sub.add_parser("weekly", help="generate and write this week's digest")
    weekly.add_argument(
        "--dry-run",
        action="store_true",
        help="print the digest outline without writing files or recording seen items",
    )
    weekly.add_argument("--notify", action="store_true", help="also post to configured webhooks")
    weekly.set_defaults(func=cmd_weekly)

    fetch = sub.add_parser("fetch", help="fetch and rank only, no LLM calls")
    fetch.add_argument("-n", "--limit", type=int, default=30, help="rows to print")
    fetch.set_defaults(func=cmd_fetch)

    providers = sub.add_parser("providers", help="show LLM provider status")
    providers.add_argument("--probe", action="store_true", help="send a one-token test request")
    providers.set_defaults(func=cmd_providers)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
