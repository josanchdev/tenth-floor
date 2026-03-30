"""Unit tests for agent modules — mocked LLM calls, no real inference."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tenth_floor.data.models import (
    PairSnapshot,
    PlaybookVerdict,
    QuantSignal,
    SentimentBias,
    SentimentSignal,
    SentimentSnapshot,
    SetupAction,
    SetupProposal,
    SignalDirection,
    TAIndicators,
    TrendRegime,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_indicators() -> TAIndicators:
    return TAIndicators(
        ema_20=62000.0, ema_50=61500.0, ema_200=59000.0,
        rsi_14=55.0,
        macd_line=100.0, macd_signal=80.0, macd_histogram=20.0,
        bb_upper=63000.0, bb_middle=62000.0, bb_lower=61000.0,
        atr_14=500.0,
        obv=1000000.0, volume_sma_20=50000.0,
    )


@pytest.fixture
def sample_snapshot(sample_indicators: TAIndicators) -> PairSnapshot:
    return PairSnapshot(
        symbol="BTCUSDT",
        timeframe="1d",
        current_price=62000.0,
        bar_timestamp=1710374400000,
        indicators=sample_indicators,
        sentiment=None,
        recent_closes=[62000, 61800, 61500, 61200, 61000],
        recent_volumes=[500, 480, 520, 510, 490],
    )


@pytest.fixture
def sample_sentiment_snapshot() -> SentimentSnapshot:
    return SentimentSnapshot(
        fear_greed_value=65,
        fear_greed_label="Greed",
        fear_greed_trend=[65, 60, 55],
        headlines=[],
    )


@pytest.fixture
def sample_quant_signal() -> QuantSignal:
    return QuantSignal(
        symbol="BTCUSDT",
        timeframe="1d",
        trend_regime=TrendRegime.UPTREND,
        signals=["EMA golden cross (20 > 50)", "Price above 200 EMA"],
        confidence=0.78,
        reasoning="Strong uptrend with EMA alignment.",
    )


@pytest.fixture
def sample_sentiment_signal() -> SentimentSignal:
    return SentimentSignal(
        bias=SentimentBias.GREED,
        risk_narrative="Market sentiment is moderately bullish.",
        key_headlines=["BTC rallies past 62k"],
        fear_greed_value=65,
    )


@pytest.fixture
def sample_proposal() -> SetupProposal:
    return SetupProposal(
        symbol="BTCUSDT",
        timeframe="1d",
        direction=SignalDirection.LONG,
        action=SetupAction.BUY,
        entry_zone_low=61690.0,
        entry_zone_high=62310.0,
        stop_loss=61090.0,
        take_profit=63510.0,
        reward_risk_ratio=2.0,
        rationale="Strong uptrend setup.",
        confluence_factors=["EMA alignment", "Volume support"],
    )


# ---------------------------------------------------------------------------
# QuantAgent tests
# ---------------------------------------------------------------------------


class TestQuantAgent:
    def test_run_returns_quant_signal(self, sample_snapshot: PairSnapshot) -> None:
        """QuantAgent.run() returns a QuantSignal from mocked LLM."""
        mock_response = json.dumps({
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "trend_regime": "uptrend",
            "signals": ["Price above 200 EMA"],
            "confidence": 0.75,
            "reasoning": "Bullish trend.",
        })

        with patch("tenth_floor.agents.quant_agent.call_llm", return_value=mock_response):
            from tenth_floor.agents.quant_agent import QuantAgent
            agent = QuantAgent()
            result = agent.run(sample_snapshot)

        assert isinstance(result, QuantSignal)
        assert result.trend_regime == TrendRegime.UPTREND
        assert result.confidence == 0.75

    def test_prompt_contains_indicators(self, sample_snapshot: PairSnapshot) -> None:
        """Prompt should include TA indicator values."""
        from tenth_floor.agents.quant_agent import QuantAgent
        prompt = QuantAgent._build_prompt(sample_snapshot)
        assert "62000.0" in prompt  # EMA 20
        assert "RSI 14: 55.0" in prompt


# ---------------------------------------------------------------------------
# SentimentAgent tests
# ---------------------------------------------------------------------------


class TestSentimentAgent:
    def test_run_returns_sentiment_signal(
        self, sample_sentiment_snapshot: SentimentSnapshot
    ) -> None:
        mock_response = json.dumps({
            "bias": "greed",
            "risk_narrative": "Market is greedy.",
            "key_headlines": [],
            "fear_greed_value": 65,
        })

        with patch("tenth_floor.agents.sentiment_agent.call_llm", return_value=mock_response):
            from tenth_floor.agents.sentiment_agent import SentimentAgent
            agent = SentimentAgent()
            result = agent.run(sample_sentiment_snapshot)

        assert isinstance(result, SentimentSignal)
        assert result.bias == SentimentBias.GREED
        assert result.fear_greed_value == 65


# ---------------------------------------------------------------------------
# StrategyAgent tests
# ---------------------------------------------------------------------------


class TestStrategyAgent:
    def test_run_returns_setup_proposal(
        self,
        sample_snapshot: PairSnapshot,
        sample_quant_signal: QuantSignal,
        sample_sentiment_signal: SentimentSignal,
    ) -> None:
        mock_response = json.dumps({
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "direction": "long",
            "action": "buy",
            "entry_zone_low": 61690.0,
            "entry_zone_high": 62310.0,
            "stop_loss": 61090.0,
            "take_profit": 63510.0,
            "reward_risk_ratio": 2.0,
            "rationale": "Strong setup.",
            "confluence_factors": ["EMA alignment"],
        })

        with patch("tenth_floor.agents.strategy_agent.call_llm", return_value=mock_response):
            from tenth_floor.agents.strategy_agent import StrategyAgent
            agent = StrategyAgent()
            result = agent.run(sample_snapshot, sample_quant_signal, sample_sentiment_signal)

        assert isinstance(result, SetupProposal)
        assert result.direction == SignalDirection.LONG
        assert result.action == SetupAction.BUY

    def test_short_override_to_skip(
        self,
        sample_snapshot: PairSnapshot,
        sample_quant_signal: QuantSignal,
        sample_sentiment_signal: SentimentSignal,
    ) -> None:
        """If LLM returns SHORT, StrategyAgent must override to SKIP."""
        mock_response = json.dumps({
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "direction": "short",
            "action": "sell",
            "entry_zone_low": 61690.0,
            "entry_zone_high": 62310.0,
            "stop_loss": 63000.0,
            "take_profit": 60000.0,
            "reward_risk_ratio": 2.0,
            "rationale": "Bearish signal.",
            "confluence_factors": [],
        })

        with patch("tenth_floor.agents.strategy_agent.call_llm", return_value=mock_response):
            from tenth_floor.agents.strategy_agent import StrategyAgent
            agent = StrategyAgent()
            result = agent.run(sample_snapshot, sample_quant_signal, sample_sentiment_signal)

        # Must be overridden to SKIP, not SHORT
        assert result.direction == SignalDirection.NEUTRAL
        assert result.action == SetupAction.SKIP


class TestComputePriceLevels:
    """Test that _compute_price_levels uses market-price entry with structural SL/TP."""

    def _make_agent(self):
        with patch("tenth_floor.agents.strategy_agent.load_agent_config", return_value={}):
            from tenth_floor.agents.strategy_agent import StrategyAgent
            return StrategyAgent()

    def test_entry_at_market_price(self) -> None:
        """Entry zone should be near current price, not anchored to support."""
        indicators = TAIndicators(
            ema_20=62000.0, ema_50=61500.0, ema_200=59000.0,
            rsi_14=55.0, macd_line=100.0, macd_signal=80.0, macd_histogram=20.0,
            bb_upper=63000.0, bb_middle=62000.0, bb_lower=61000.0,
            atr_14=500.0, obv=1000000.0, volume_sma_20=50000.0,
            support_levels=[61200.0, 61700.0],
            resistance_levels=[62800.0, 63500.0],
        )
        snap = PairSnapshot(
            symbol="BTCUSDT", timeframe="1d",
            current_price=62000.0, bar_timestamp=1710374400000,
            indicators=indicators,
        )
        agent = self._make_agent()
        levels = agent._compute_price_levels(snap)

        # Entry high = spot price, entry low = spot - 0.25*ATR
        assert levels["entry_zone_high"] == 62000.0
        assert levels["entry_zone_low"] == pytest.approx(62000.0 - 500.0 * 0.25)
        # SL should be below nearest support (61700)
        assert levels["stop_loss"] < 61700.0
        # TP should target resistance at 62800
        assert levels["take_profit"] == 62800.0

    def test_tp_targets_resistance(self) -> None:
        """TP should use resistance level when R:R >= minimum."""
        indicators = TAIndicators(
            ema_20=100.0, ema_50=98.0, ema_200=95.0,
            rsi_14=50.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5,
            bb_upper=105.0, bb_middle=100.0, bb_lower=95.0,
            atr_14=3.0, obv=100000.0, volume_sma_20=5000.0,
            support_levels=[98.5],
            resistance_levels=[108.0],
        )
        snap = PairSnapshot(
            symbol="SOLUSDT", timeframe="1d",
            current_price=100.0, bar_timestamp=1710374400000,
            indicators=indicators,
        )
        agent = self._make_agent()
        levels = agent._compute_price_levels(snap)

        # Entry_mid ≈ 99.625, SL below support 98.5 (≈ 97.6)
        # R:R = (108-99.625)/(99.625-97.6) ≈ 4.1 — well above 2.0
        assert levels["take_profit"] == 108.0
        assert levels["reward_risk_ratio"] > 2.0

    def test_fallback_when_no_sr(self) -> None:
        """Without S/R levels, falls back to ATR-based formula."""
        indicators = TAIndicators(
            ema_20=62000.0, ema_50=61500.0, ema_200=59000.0,
            rsi_14=55.0, macd_line=100.0, macd_signal=80.0, macd_histogram=20.0,
            bb_upper=63000.0, bb_middle=62000.0, bb_lower=61000.0,
            atr_14=500.0, obv=1000000.0, volume_sma_20=50000.0,
            support_levels=[], resistance_levels=[],
        )
        snap = PairSnapshot(
            symbol="BTCUSDT", timeframe="1d",
            current_price=62000.0, bar_timestamp=1710374400000,
            indicators=indicators,
        )
        agent = self._make_agent()
        levels = agent._compute_price_levels(snap)

        # entry_high = price, entry_low = price - 0.25*ATR
        assert levels["entry_zone_high"] == 62000.0
        assert levels["entry_zone_low"] == pytest.approx(62000.0 - 125.0)
        assert levels["reward_risk_ratio"] >= 2.0

    def test_rr_varies_with_structure(self) -> None:
        """R:R should NOT always be exactly 2.0 when S/R levels are present."""
        indicators = TAIndicators(
            ema_20=100.0, ema_50=98.0, ema_200=95.0,
            rsi_14=50.0, macd_line=1.0, macd_signal=0.5, macd_histogram=0.5,
            bb_upper=105.0, bb_middle=100.0, bb_lower=95.0,
            atr_14=3.0, obv=100000.0, volume_sma_20=5000.0,
            support_levels=[98.5],
            resistance_levels=[110.0],
        )
        snap = PairSnapshot(
            symbol="SOLUSDT", timeframe="1d",
            current_price=100.0, bar_timestamp=1710374400000,
            indicators=indicators,
        )
        agent = self._make_agent()
        levels = agent._compute_price_levels(snap)

        # With real S/R, R:R should vary — not be pinned to exactly 2.0
        assert levels["reward_risk_ratio"] != 2.0
        assert levels["reward_risk_ratio"] >= 2.0


# ---------------------------------------------------------------------------
# RiskAgent tests
# ---------------------------------------------------------------------------


class TestRiskAgent:
    def test_approve_valid_long(self, sample_proposal: SetupProposal) -> None:
        """High-confidence LONG → APPROVED, conviction=high, risk=2%."""
        with patch("tenth_floor.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Strong setup approved."}]'):
            from tenth_floor.agents.risk_agent import RiskAgent
            agent = RiskAgent()
            entries = agent.run([(sample_proposal, 0.82)])

        assert len(entries) == 1
        assert entries[0].verdict == PlaybookVerdict.APPROVED
        assert entries[0].conviction == "high"
        assert entries[0].suggested_risk_pct == 0.02
        assert entries[0].confidence_score == 0.82

    def test_approve_standard_conviction(self, sample_proposal: SetupProposal) -> None:
        """Mid-confidence LONG → APPROVED, conviction=standard, risk=1%."""
        with patch("tenth_floor.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Acceptable setup."}]'):
            from tenth_floor.agents.risk_agent import RiskAgent
            agent = RiskAgent()
            entries = agent.run([(sample_proposal, 0.71)])

        assert len(entries) == 1
        assert entries[0].verdict == PlaybookVerdict.APPROVED
        assert entries[0].conviction == "standard"
        assert entries[0].suggested_risk_pct == 0.01
        assert entries[0].confidence_score == 0.71

    def test_reject_short(self) -> None:
        """SHORT proposal → REJECTED with 'Spot only'."""
        short_proposal = SetupProposal(
            symbol="BTCUSDT", timeframe="1d",
            direction=SignalDirection.SHORT, action=SetupAction.SELL,
            entry_zone_low=62000, entry_zone_high=62500,
            stop_loss=63000, take_profit=60000,
            reward_risk_ratio=2.0, rationale="Bearish.",
        )

        with patch("tenth_floor.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Rejected."}]'):
            from tenth_floor.agents.risk_agent import RiskAgent
            agent = RiskAgent()
            entries = agent.run([(short_proposal, 0.85)])

        assert entries[0].verdict == PlaybookVerdict.REJECTED
        assert "Spot only" in entries[0].verdict_reasoning

    def test_reject_low_confidence(self, sample_proposal: SetupProposal) -> None:
        """Confidence below 0.65 threshold → REJECTED."""
        with patch("tenth_floor.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Low confidence."}]'):
            from tenth_floor.agents.risk_agent import RiskAgent
            agent = RiskAgent()
            entries = agent.run([(sample_proposal, 0.50)])

        assert entries[0].verdict == PlaybookVerdict.REJECTED
        assert "Confidence" in entries[0].verdict_reasoning
        assert entries[0].conviction == "none"
        assert entries[0].suggested_risk_pct == 0.0

    def test_reject_low_rr(self) -> None:
        """R:R below configured minimum (2.0) → REJECTED."""
        low_rr_proposal = SetupProposal(
            symbol="BTCUSDT", timeframe="1d",
            direction=SignalDirection.LONG, action=SetupAction.BUY,
            entry_zone_low=62000, entry_zone_high=62500,
            stop_loss=60500, take_profit=63500,
            reward_risk_ratio=1.5, rationale="Weak R:R.",
        )

        with patch("tenth_floor.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Low R:R."}]'):
            from tenth_floor.agents.risk_agent import RiskAgent
            agent = RiskAgent()
            entries = agent.run([(low_rr_proposal, 0.82)])

        assert entries[0].verdict == PlaybookVerdict.REJECTED
        assert "R:R" in entries[0].verdict_reasoning


# ---------------------------------------------------------------------------
# base.py utility tests
# ---------------------------------------------------------------------------


class TestCleanJsonResponse:
    def test_strips_think_tags(self) -> None:
        """Qwen3 <think> blocks should be stripped."""
        from tenth_floor.agents.base import clean_json_response
        raw = '<think>Let me analyze this...</think>{"key": "value"}'
        assert clean_json_response(raw) == '{"key": "value"}'

    def test_strips_code_fences(self) -> None:
        from tenth_floor.agents.base import clean_json_response
        raw = '```json\n{"key": "value"}\n```'
        assert clean_json_response(raw) == '{"key": "value"}'

    def test_strips_both(self) -> None:
        from tenth_floor.agents.base import clean_json_response
        raw = '<think>thinking...</think>\n```json\n{"key": "value"}\n```'
        assert clean_json_response(raw) == '{"key": "value"}'

    def test_passthrough_clean_json(self) -> None:
        from tenth_floor.agents.base import clean_json_response
        raw = '{"key": "value"}'
        assert clean_json_response(raw) == '{"key": "value"}'


class TestParseJsonResponse:
    def test_valid_quant_signal(self) -> None:
        from tenth_floor.agents.base import parse_json_response
        raw = json.dumps({
            "symbol": "BTCUSDT", "timeframe": "4h",
            "trend_regime": "uptrend",
            "signals": ["Price above 200 EMA"],
            "confidence": 0.75, "reasoning": "Bullish.",
        })
        result = parse_json_response(raw, QuantSignal)
        assert result.confidence == 0.75

    def test_invalid_json_raises(self) -> None:
        from tenth_floor.agents.base import parse_json_response
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_json_response("not json at all", QuantSignal)

    def test_schema_mismatch_raises(self) -> None:
        from tenth_floor.agents.base import parse_json_response
        with pytest.raises(ValueError, match="Schema validation"):
            parse_json_response('{"wrong": "schema"}', QuantSignal)
