"""
Pydantic v2 models — typed contracts for every inter-module boundary.

Layer overview
--------------
Data Layer    : OHLCVBar, SentimentSnapshot
Feature Engine: TAIndicators, PairSnapshot
Agent Outputs : MacroSignal, TradeProposal, ReviewedSignal, PlaybookEntry

Design rules:
  • Python computes indicators as context — LLM makes all trading decisions.
  • Python validates LLM output for sanity (SL < entry < TP, R:R math).
  • Every model can round-trip through JSON (for Langfuse tracing).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# =====================================================================
# Enums – shared across layers
# =====================================================================


class SignalDirection(StrEnum):
    """Directional bias for a trade setup."""

    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class SetupAction(StrEnum):
    """What kind of trade action the agent proposes. Spot LONG only."""

    BUY = "buy"
    SKIP = "skip"


class PlaybookVerdict(StrEnum):
    """RiskReviewer's final verdict on a setup."""

    APPROVED = "approved"
    REJECTED = "rejected"


class MacroRegime(StrEnum):
    """Market macro regime from MacroAnalyst."""

    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    MIXED = "mixed"
    TRANSITIONING = "transitioning"


class ReviewVerdict(StrEnum):
    """RiskReviewer's verdict on a trade proposal."""

    APPROVE = "approve"
    REJECT = "reject"


class ConvictionTier(StrEnum):
    """RiskReviewer's conviction tier for a trade."""

    HIGH = "high"
    STANDARD = "standard"


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
        return datetime.fromtimestamp(self.timestamp / 1000, tz=UTC)


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
        default_factory=lambda: datetime.now(UTC),
        description="When this snapshot was captured",
    )


# =====================================================================
# 2 – Feature Engine  (src/features/)
# =====================================================================


class TAIndicators(BaseModel):
    """Technical analysis indicators computed by ``ta_calculator.py``.

    All values are computed by Python (pandas-ta). These are INPUT CONTEXT
    for LLM agents — the LLM uses them for reasoning but makes its own
    trading decisions.
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

    # Reversal detection
    rsi_divergence: bool | None = Field(
        None,
        description=(
            "Bullish RSI divergence: price made a lower low but RSI made a "
            "higher low. Classic capitulation / bottoming signal."
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
    This is the **only** input contract for TradeAnalyst.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    symbol: str = Field(..., description="Trading pair, e.g. 'BTCUSDT' or 'AAPL'")
    timeframe: str = Field(..., description="Candle timeframe, e.g. '1d'")
    asset_class: str | None = Field(
        None, description="Asset class: 'crypto', 'equity', 'etf', 'commodity'"
    )

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
        return datetime.fromtimestamp(self.bar_timestamp / 1000, tz=UTC)


# =====================================================================
# 3 – Agent Outputs (Phase 1.5: AI-first architecture)
# =====================================================================
#
# LLM agents make all trading decisions. Python validates output
# for sanity but does not override. Three agents produce:
#   MacroAnalyst  → MacroSignal
#   TradeAnalyst  → TradeProposal
#   RiskReviewer  → ReviewedSignal
# =====================================================================


class AssetClassImpact(BaseModel):
    """Per-asset-class macro impact from MacroAnalyst."""

    model_config = ConfigDict(frozen=True)

    asset_class: str = Field(..., description="e.g. 'crypto', 'equity', 'etf', 'commodity'")
    outlook: str = Field(..., description="'bullish', 'bearish', or 'neutral'")
    reasoning: str = Field(..., description="1-2 sentences on why")


class MacroSignal(BaseModel):
    """Output of MacroAnalyst — macro regime and per-class impact.

    Runs once per pipeline. Its output frames every TradeAnalyst call.
    """

    model_config = ConfigDict(frozen=True)

    regime: MacroRegime = Field(..., description="Overall market regime")
    regime_reasoning: str = Field(..., description="2-3 sentences explaining the regime call")
    asset_class_impacts: list[AssetClassImpact] = Field(
        default_factory=list,
        description="Per-asset-class outlook and reasoning",
    )
    alerts: list[str] = Field(
        default_factory=list,
        description="Specific alerts: earnings, news catalysts, macro events",
    )
    vix_level: float | None = Field(None, description="Current VIX level")
    fear_greed_value: int = Field(
        ..., ge=0, le=100, description="Current F&G index value"
    )
    dxy_trend: str = Field(
        ..., description="'strengthening', 'weakening', or 'stable'"
    )


class TradeProposal(BaseModel):
    """Output of TradeAnalyst — LLM-decided trade setup.

    The LLM picks entry, SL, TP based on structural analysis. Python
    validates the output for sanity but does not override levels.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    action: SetupAction = Field(..., description="'buy' or 'skip'")
    direction: SignalDirection = Field(..., description="'long' or 'neutral'")

    # LLM-chosen price levels (may be 0 for SKIPs — Python validation
    # only runs on BUY proposals so the gt=0 check lives there, not here)
    entry_zone_low: float = Field(..., ge=0, description="Lower bound of entry zone")
    entry_zone_high: float = Field(..., ge=0, description="Upper bound of entry zone")
    stop_loss: float = Field(..., ge=0, description="Stop-loss price")
    take_profit: float = Field(..., ge=0, description="Take-profit price")

    # LLM assessment
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="TradeAnalyst's confidence in this setup (0-1)",
    )
    rationale: str = Field(..., description="Full trade thesis — 3-5 sentences")
    entry_reasoning: str = Field("", description="Why this entry zone")
    stop_reasoning: str = Field("", description="Why this stop-loss level")
    target_reasoning: str = Field("", description="Why this take-profit level")
    confluence_factors: list[str] = Field(
        default_factory=list,
        description="Supporting factors for this trade",
    )
    risk_factors: list[str] = Field(
        default_factory=list,
        description="Risks and what could go wrong",
    )


class ReviewedSignal(BaseModel):
    """Per-proposal verdict from RiskReviewer.

    RiskReviewer sees ALL proposals at once and makes portfolio-level
    decisions. This is a single item in the array it returns.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    verdict: ReviewVerdict = Field(..., description="'approve' or 'reject'")
    conviction: ConvictionTier = Field(..., description="'high' or 'standard'")
    reasoning: str = Field(..., description="Why approved/rejected — portfolio context")
    risk_notes: str = Field("", description="Specific risk flags for this signal")


# =====================================================================
# 4 – PlaybookEntry (final output contract for DB + Discord)
# =====================================================================


class PlaybookEntry(BaseModel):
    """Final vetted signal for the daily playbook.

    Assembled by the pipeline from TradeProposal + ReviewedSignal.
    This is the contract that signal_logger and discord_notifier consume.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    symbol: str
    timeframe: str
    report_date: str = Field(
        ..., description="ISO date of the playbook, e.g. '2026-03-14'"
    )

    # Verdict (from RiskReviewer)
    verdict: PlaybookVerdict = Field(
        ..., description="RiskReviewer's final call"
    )
    verdict_reasoning: str = Field(
        ..., description="Why the signal was approved or rejected"
    )

    # Trade setup (from TradeAnalyst, validated by Python)
    direction: SignalDirection
    entry_zone_low: float = Field(..., gt=0)
    entry_zone_high: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    reward_risk_ratio: float = Field(..., gt=0)

    # Conviction (from RiskReviewer)
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0,
        description="TradeAnalyst confidence score (0–1)",
    )
    conviction: ConvictionTier = Field(
        ..., description="Conviction tier: 'high' or 'standard'"
    )
    suggested_risk_pct: float = Field(
        ..., ge=0.0, le=1.0,
        description="Suggested portfolio risk per trade (e.g. 0.02 = 2%)",
    )

    # Reasoning
    rationale: str = Field("", description="TradeAnalyst trade thesis")
    risk_notes: str = Field("", description="RiskReviewer risk flags")

    # Priority
    rank: int = Field(
        ..., ge=1,
        description="Priority rank within the playbook (1 = highest)",
    )
