"""
Pydantic v2 models — typed contracts for every inter-module boundary.

Layer overview
--------------
Data Layer    : OHLCVBar, SentimentSnapshot
Feature Engine: TAIndicators, PairSnapshot
Agent Outputs : QuantSignal, SentimentSignal, SetupProposal, PlaybookEntry

Design rules (from docs/v1_risks_and_constraints.md §2):
  • Python computes all numbers → models carry pre-computed values only.
  • Agent output schemas have NO free-form numeric fields —
    only enums, labels, and quoted snapshot values.
  • Every model can round-trip through JSON (for Langfuse tracing).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# =====================================================================
# Enums – shared across layers
# =====================================================================


class TrendRegime(str, Enum):
    """Market trend classification produced by QuantAgent."""

    STRONG_UPTREND = "strong_uptrend"
    UPTREND = "uptrend"
    SIDEWAYS = "sideways"
    DOWNTREND = "downtrend"
    STRONG_DOWNTREND = "strong_downtrend"


class SignalDirection(str, Enum):
    """Directional bias for a trade setup."""

    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class SentimentBias(str, Enum):
    """Macro sentiment bias label from SentimentAgent."""

    EXTREME_GREED = "extreme_greed"
    GREED = "greed"
    NEUTRAL = "neutral"
    FEAR = "fear"
    EXTREME_FEAR = "extreme_fear"


class SetupAction(str, Enum):
    """What kind of trade action the strategy proposes."""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    SKIP = "skip"


class PlaybookVerdict(str, Enum):
    """RiskAgent's final verdict on a setup."""

    APPROVED = "approved"
    REJECTED = "rejected"


# =====================================================================
# 1 – Data Layer  (src/data/)
# =====================================================================


class OHLCVBar(BaseModel):
    """Single OHLCV candle bar as fetched from Binance via ccxt.

    ``timestamp`` is Unix **milliseconds** (matches ccxt output).
    """

    model_config = ConfigDict(frozen=True)

    timestamp: int = Field(..., description="Bar open time in Unix ms")
    open: float = Field(..., ge=0)
    high: float = Field(..., ge=0)
    low: float = Field(..., ge=0)
    close: float = Field(..., ge=0)
    volume: float = Field(..., ge=0)

    @property
    def dt_utc(self) -> datetime:
        """Convenience: bar open time as a UTC datetime."""
        return datetime.fromtimestamp(self.timestamp / 1000, tz=timezone.utc)


class RSSHeadline(BaseModel):
    """A single RSS headline used for sentiment context."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(..., description="Headline text")
    source: str = Field(..., description="Feed name, e.g. 'coindesk'")
    published: datetime | None = Field(None, description="Publication time (UTC)")
    url: str | None = Field(None, description="Article URL")


class SentimentSnapshot(BaseModel):
    """Aggregated sentiment data used as agent input.

    Combines the Fear & Greed index with recent RSS headlines.
    """

    model_config = ConfigDict(frozen=True)

    fear_greed_value: int = Field(
        ..., ge=0, le=100, description="Current Fear & Greed index (0 = extreme fear, 100 = extreme greed)"
    )
    fear_greed_label: str = Field(
        ..., description="Human label, e.g. 'Fear', 'Greed'"
    )
    fear_greed_trend: list[int] = Field(
        default_factory=list,
        description="Last N days of F&G values (most recent first) for trend context",
    )
    headlines: list[RSSHeadline] = Field(
        default_factory=list, description="Recent RSS headlines (capped per services.yaml)"
    )
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this snapshot was captured",
    )


# =====================================================================
# 2 – Feature Engine  (src/features/)
# =====================================================================


class TAIndicators(BaseModel):
    """Technical analysis indicators computed by ``ta_calculator.py``.

    All values are computed by Python (pandas-ta).  LLMs must consume
    these as-is — they MUST NOT recompute or derive any numbers.
    """

    model_config = ConfigDict(frozen=True)

    # EMAs
    ema_20: float | None = Field(None, description="EMA 20-period")
    ema_50: float | None = Field(None, description="EMA 50-period")
    ema_200: float | None = Field(None, description="EMA 200-period")

    # RSI
    rsi_14: float | None = Field(None, ge=0, le=100, description="RSI 14-period")

    # MACD
    macd_line: float | None = Field(None, description="MACD line")
    macd_signal: float | None = Field(None, description="MACD signal line")
    macd_histogram: float | None = Field(None, description="MACD histogram")

    # Bollinger Bands
    bb_upper: float | None = Field(None, description="Bollinger Band upper (20, 2)")
    bb_middle: float | None = Field(None, description="Bollinger Band middle (SMA 20)")
    bb_lower: float | None = Field(None, description="Bollinger Band lower (20, 2)")

    # ATR
    atr_14: float | None = Field(None, ge=0, description="ATR 14-period")

    # Volume
    obv: float | None = Field(None, description="On-Balance Volume")
    volume_sma_20: float | None = Field(None, ge=0, description="20-period SMA of volume")
    volume_ratio: float | None = Field(
        None, ge=0,
        description="Latest bar volume / 20-period SMA volume. >1.5 = notable, >2.0 = surge",
    )

    # Deterministic trend score (Python-computed, not LLM)
    trend_score: float | None = Field(
        None, ge=0.0, le=1.0,
        description=(
            "Deterministic indicator-agreement score (0–1). "
            "Replaces LLM confidence for gating and conviction tiers."
        ),
    )

    # Structure — swing highs/lows detected from price pivots
    support_levels: list[float] = Field(
        default_factory=list,
        description="Recent swing low prices (ascending), used to anchor SL and entry zones",
    )
    resistance_levels: list[float] = Field(
        default_factory=list,
        description="Recent swing high prices (ascending), used to anchor TP targets",
    )


class PairSnapshot(BaseModel):
    """The single typed payload that every agent receives.

    Assembled by ``pair_snapshot.py`` from OHLCV + TA + sentiment.
    This is the **only** input contract for QuantAgent and SentimentAgent.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    symbol: str = Field(..., description="Trading pair, e.g. 'BTCUSDT'")
    timeframe: str = Field(..., description="Candle timeframe, e.g. '4h' or '1d'")

    # Latest bar
    current_price: float = Field(..., gt=0, description="Most recent close price")
    bar_timestamp: int = Field(..., description="Timestamp of latest bar (Unix ms)")

    # TA indicators (pre-computed, immutable)
    indicators: TAIndicators

    # Sentiment (shared across all pairs per run)
    sentiment: SentimentSnapshot | None = Field(
        None, description="Sentiment context (same for all pairs in a run)"
    )

    # Historical context (for trend detection prompts)
    recent_closes: list[float] = Field(
        default_factory=list,
        description="Last 20 close prices (newest first) for visual context",
    )
    recent_volumes: list[float] = Field(
        default_factory=list,
        description="Last 20 volumes (newest first) for volume context",
    )

    @property
    def dt_utc(self) -> datetime:
        """Latest bar time as UTC datetime."""
        return datetime.fromtimestamp(self.bar_timestamp / 1000, tz=timezone.utc)


# =====================================================================
# 3 – Agent Outputs  (src/agents/)
# =====================================================================
#
# Rule: NO free-form numeric fields. Agents receive pre-computed
#       numbers and may only quote them back, never recompute.
# =====================================================================


class QuantSignal(BaseModel):
    """Output of QuantAgent — trend regime and signal classification.

    All numeric references are quoted from the input PairSnapshot.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    trend_regime: TrendRegime = Field(
        ..., description="Classified trend regime"
    )
    signals: list[str] = Field(
        default_factory=list,
        description="List of signal labels, e.g. ['RSI oversold', 'EMA golden cross']",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Confidence score (0–1). Computed by Python from indicator consensus.",
    )
    reasoning: str = Field(
        ..., description="LLM-generated reasoning for the classification"
    )


class SentimentSignal(BaseModel):
    """Output of SentimentAgent — macro risk narrative and bias."""

    model_config = ConfigDict(frozen=True)

    bias: SentimentBias = Field(
        ..., description="Macro sentiment bias label"
    )
    risk_narrative: str = Field(
        ..., description="LLM-generated narrative describing market sentiment"
    )
    key_headlines: list[str] = Field(
        default_factory=list,
        description="Most impactful headline titles selected by the agent",
    )
    fear_greed_value: int = Field(
        ..., ge=0, le=100,
        description="Quoted F&G value from snapshot (not recomputed)",
    )


class SetupProposal(BaseModel):
    """Output of StrategyAgent — a proposed trade setup with reasoning.

    Entry, stop-loss, and take-profit are quoted from snapshot values
    and risk_profile.json parameters computed by Python.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    direction: SignalDirection = Field(
        ..., description="Proposed trade direction"
    )
    action: SetupAction = Field(
        ..., description="Proposed action"
    )

    # Price levels (computed by Python, quoted by agent)
    entry_zone_low: float = Field(
        ..., gt=0, description="Lower bound of entry zone"
    )
    entry_zone_high: float = Field(
        ..., gt=0, description="Upper bound of entry zone"
    )
    stop_loss: float = Field(
        ..., gt=0, description="Stop-loss price (ATR-based, computed by Python)"
    )
    take_profit: float = Field(
        ..., gt=0, description="Take-profit price (R:R-based, computed by Python)"
    )

    # Context
    reward_risk_ratio: float = Field(
        ..., gt=0, description="Reward-to-risk ratio (computed by Python)"
    )
    rationale: str = Field(
        ..., description="LLM-generated reasoning for this setup"
    )
    confluence_factors: list[str] = Field(
        default_factory=list,
        description="List of supporting factors, e.g. ['RSI divergence', 'EMA support']",
    )


class PlaybookEntry(BaseModel):
    """Output of RiskAgent — final vetted signal for the daily playbook.

    Represents one publishable (or rejected) signal. Approved entries
    carry conviction tier and suggested risk percentage instead of
    EUR position sizing.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    symbol: str
    timeframe: str
    report_date: str = Field(
        ..., description="ISO date of the playbook, e.g. '2026-03-14'"
    )

    # Verdict
    verdict: PlaybookVerdict = Field(
        ..., description="RiskAgent's final call"
    )
    verdict_reasoning: str = Field(
        ..., description="Why the signal was approved or rejected"
    )

    # Inherited from SetupProposal (pass-through if approved)
    direction: SignalDirection
    action: SetupAction
    entry_zone_low: float = Field(..., gt=0)
    entry_zone_high: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    reward_risk_ratio: float = Field(..., gt=0)

    # Conviction tier (computed by Python from QuantAgent confidence)
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="QuantAgent confidence score (0–1)",
    )
    conviction: str = Field(
        ..., description="Conviction tier: 'high' or 'standard'"
    )
    suggested_risk_pct: float = Field(
        ..., ge=0.0, le=1.0,
        description="Suggested portfolio risk per trade (e.g. 0.02 = 2%)",
    )

    # Summaries from upstream agents
    quant_summary: str = Field("", description="Condensed QuantAgent reasoning")
    sentiment_summary: str = Field("", description="Condensed SentimentAgent reasoning")
    strategy_rationale: str = Field("", description="StrategyAgent rationale")

    # Priority
    rank: int = Field(
        ..., ge=1,
        description="Priority rank within the playbook (1 = highest)",
    )


# =====================================================================
# Convenience: DailyPlaybook (top-level report container)
# =====================================================================


class DailyPlaybook(BaseModel):
    """Top-level container for the daily report output."""

    report_date: str = Field(..., description="ISO date, e.g. '2026-03-14'")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the playbook was generated",
    )
    universe_size: int = Field(..., ge=0, description="Number of pairs analysed")
    entries: list[PlaybookEntry] = Field(
        default_factory=list, description="Sorted by rank ascending (1 = best)"
    )
    skipped_pairs: list[str] = Field(
        default_factory=list, description="Pairs that were analysed but had no actionable setup"
    )

    @property
    def approved_entries(self) -> list[PlaybookEntry]:
        """Return only approved entries (rejected are excluded)."""
        return [e for e in self.entries if e.verdict != PlaybookVerdict.REJECTED]
