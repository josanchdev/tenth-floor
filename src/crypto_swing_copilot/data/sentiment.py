"""
Fetch sentiment data: Fear & Greed index + RSS headlines.

Combines the alternative.me Fear & Greed API with CoinDesk RSS headlines
into a single ``SentimentSnapshot`` Pydantic model.

**Graceful degradation**: if either source is unreachable, the module logs
a warning and returns a partial snapshot.  The pipeline should never fail
because a sentiment API is down.

Usage as CLI test mode::

    python -m crypto_swing_copilot.data.sentiment

Usage as library::

    from crypto_swing_copilot.data.sentiment import SentimentFetcher

    fetcher = SentimentFetcher()
    snapshot = fetcher.fetch_snapshot()
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Final

import feedparser
import yaml

from crypto_swing_copilot.config import CONFIG_DIR
from crypto_swing_copilot.data.models import RSSHeadline, SentimentSnapshot

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_S: Final[int] = 10

# Fallback values when the Fear & Greed API is unreachable
_FG_DEFAULT_VALUE: Final[int] = 50
_FG_DEFAULT_LABEL: Final[str] = "Neutral"


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_sentiment_config() -> dict:
    """Load ``config/services.yaml`` and return the ``sentiment`` section."""
    path = CONFIG_DIR / "services.yaml"
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg.get("sentiment", {})


# ---------------------------------------------------------------------------
# Core fetcher
# ---------------------------------------------------------------------------


class SentimentFetcher:
    """Fetch Fear & Greed index and RSS headlines for sentiment context.

    Parameters
    ----------
    config_override:
        Optional dict to replace the ``sentiment`` section from
        ``services.yaml``.  Useful for testing.

    Notes
    -----
    - Uses ``urllib.request`` (stdlib) — no external HTTP library needed.
    - No data caching: sentiment is ephemeral and re-fetched every run.
    """

    def __init__(self, config_override: dict | None = None) -> None:
        cfg = config_override or _load_sentiment_config()

        # Fear & Greed config
        fg_cfg = cfg.get("fear_greed", {})
        self._fg_url: str = fg_cfg.get("url", "https://api.alternative.me/fng/")
        self._fg_limit: int = int(fg_cfg.get("limit", 7))

        # RSS feeds config
        self._rss_feeds: list[dict] = cfg.get("rss_feeds", [])

        logger.info(
            "SentimentFetcher initialised  fg_url=%s  fg_limit=%d  rss_feeds=%d",
            self._fg_url,
            self._fg_limit,
            len(self._rss_feeds),
        )

    # ------------------------------------------------------------------
    # Fear & Greed
    # ------------------------------------------------------------------

    def fetch_fear_greed(self) -> tuple[int, str, list[int]]:
        """Fetch Fear & Greed index from alternative.me.

        Returns
        -------
        tuple
            ``(current_value, label, trend)`` where *trend* is a list
            of the last N days' values (most recent first).
            On failure, returns ``(50, 'Neutral', [])``.
        """
        url = f"{self._fg_url}?limit={self._fg_limit}&format=json"
        logger.debug("Fetching Fear & Greed  url=%s", url)

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "crypto-swing-copilot/0.1"},
            )
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            logger.warning("Fear & Greed API unavailable: %s — using defaults", exc)
            return _FG_DEFAULT_VALUE, _FG_DEFAULT_LABEL, []
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Fear & Greed API malformed response: %s — using defaults", exc)
            return _FG_DEFAULT_VALUE, _FG_DEFAULT_LABEL, []

        entries = data.get("data", [])
        if not entries:
            logger.warning("Fear & Greed API returned empty data array")
            return _FG_DEFAULT_VALUE, _FG_DEFAULT_LABEL, []

        # Most recent entry is first in the API response
        current = entries[0]
        value = int(current.get("value", _FG_DEFAULT_VALUE))
        label = current.get("value_classification", _FG_DEFAULT_LABEL)

        # Build trend list: last N values, most recent first
        trend: list[int] = []
        for entry in entries:
            try:
                trend.append(int(entry["value"]))
            except (KeyError, ValueError):
                continue

        logger.info(
            "Fear & Greed  value=%d  label=%s  trend=%s",
            value,
            label,
            trend,
        )
        return value, label, trend

    # ------------------------------------------------------------------
    # RSS Headlines
    # ------------------------------------------------------------------

    def fetch_rss_headlines(self) -> list[RSSHeadline]:
        """Fetch and parse RSS headlines from configured feeds.

        Returns
        -------
        list[RSSHeadline]
            Combined headlines from all feeds, capped per feed
            according to ``max_items`` in ``services.yaml``.
            Returns empty list on total failure.
        """
        all_headlines: list[RSSHeadline] = []

        for feed_cfg in self._rss_feeds:
            name: str = feed_cfg.get("name", "unknown")
            url: str = feed_cfg.get("url", "")
            max_items: int = int(feed_cfg.get("max_items", 10))

            if not url:
                logger.warning("RSS feed '%s' has no URL — skipping", name)
                continue

            logger.debug("Fetching RSS  feed=%s  url=%s  max=%d", name, url, max_items)

            try:
                parsed = feedparser.parse(url)
            except Exception as exc:
                logger.warning("RSS feed '%s' failed: %s — skipping", name, exc)
                continue

            if parsed.bozo and not parsed.entries:
                logger.warning(
                    "RSS feed '%s' parse error: %s — skipping",
                    name,
                    parsed.get("bozo_exception", "unknown"),
                )
                continue

            for entry in parsed.entries[:max_items]:
                # Parse publication date
                published_dt: datetime | None = None
                published_parsed = entry.get("published_parsed")
                if published_parsed:
                    try:
                        from calendar import timegm

                        ts = timegm(published_parsed)
                        published_dt = datetime.fromtimestamp(ts, tz=UTC)
                    except (TypeError, ValueError, OverflowError):
                        pass

                headline = RSSHeadline(
                    title=entry.get("title", "").strip(),
                    source=name,
                    published=published_dt,
                    url=entry.get("link"),
                )
                all_headlines.append(headline)

            logger.info(
                "RSS  feed=%s  items=%d (capped from %d)",
                name,
                min(len(parsed.entries), max_items),
                len(parsed.entries),
            )

        logger.info("Total RSS headlines fetched: %d", len(all_headlines))
        return all_headlines

    # ------------------------------------------------------------------
    # Combined snapshot
    # ------------------------------------------------------------------

    def fetch_snapshot(self) -> SentimentSnapshot:
        """Fetch all sentiment data and return a typed ``SentimentSnapshot``.

        Combines Fear & Greed index and RSS headlines into a single
        Pydantic model ready for agent consumption.
        """
        value, label, trend = self.fetch_fear_greed()
        headlines = self.fetch_rss_headlines()

        snapshot = SentimentSnapshot(
            fear_greed_value=value,
            fear_greed_label=label,
            fear_greed_trend=trend,
            headlines=headlines,
        )

        logger.info(
            "SentimentSnapshot built  fg=%d/%s  headlines=%d  fetched_at=%s",
            snapshot.fear_greed_value,
            snapshot.fear_greed_label,
            len(snapshot.headlines),
            snapshot.fetched_at.isoformat(),
        )
        return snapshot


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------


def _cli_main() -> None:
    """Quick test mode — run from the command line to validate fetching.

    Usage::

        python -m crypto_swing_copilot.data.sentiment
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    console.print("\n[bold cyan]crypto-swing-copilot · Sentiment Fetcher[/bold cyan]\n")

    fetcher = SentimentFetcher()
    snapshot = fetcher.fetch_snapshot()

    # ---- Fear & Greed panel ----
    fg_color = (
        "red" if snapshot.fear_greed_value < 25
        else "yellow" if snapshot.fear_greed_value < 50
        else "green" if snapshot.fear_greed_value < 75
        else "bold green"
    )
    trend_str = " → ".join(str(v) for v in snapshot.fear_greed_trend) or "—"

    fg_content = (
        f"[{fg_color}]{snapshot.fear_greed_value}[/{fg_color}]  "
        f"({snapshot.fear_greed_label})\n\n"
        f"7-day trend: {trend_str}"
    )
    console.print(Panel(fg_content, title="Fear & Greed Index", border_style="cyan"))

    # ---- Headlines table ----
    if snapshot.headlines:
        table = Table(title="RSS Headlines", show_lines=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("Source", style="cyan", no_wrap=True)
        table.add_column("Title", style="white", max_width=70)
        table.add_column("Published", style="dim", no_wrap=True)

        for i, hl in enumerate(snapshot.headlines, 1):
            pub = hl.published.strftime("%Y-%m-%d %H:%M") if hl.published else "—"
            table.add_row(str(i), hl.source, hl.title, pub)

        console.print(table)
    else:
        console.print("[yellow]No headlines fetched.[/yellow]")

    console.print(f"\n[dim]Fetched at: {snapshot.fetched_at.isoformat()}[/dim]")
    console.print("[bold green]✓ Done.[/bold green]\n")


if __name__ == "__main__":
    _cli_main()
