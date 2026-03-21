"""Unit tests for agent modules — mocked LLM calls, no real inference."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from crypto_swing_copilot.data.models import (
    PairSnapshot,
    PlaybookEntry,
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
        timeframe="4h",
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
        timeframe="4h",
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
        timeframe="4h",
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

        with patch("crypto_swing_copilot.agents.quant_agent.call_llm", return_value=mock_response):
            from crypto_swing_copilot.agents.quant_agent import QuantAgent
            agent = QuantAgent()
            result = agent.run(sample_snapshot)

        assert isinstance(result, QuantSignal)
        assert result.trend_regime == TrendRegime.UPTREND
        assert result.confidence == 0.75

    def test_prompt_contains_indicators(self, sample_snapshot: PairSnapshot) -> None:
        """Prompt should include TA indicator values."""
        from crypto_swing_copilot.agents.quant_agent import QuantAgent
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

        with patch("crypto_swing_copilot.agents.sentiment_agent.call_llm", return_value=mock_response):
            from crypto_swing_copilot.agents.sentiment_agent import SentimentAgent
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

        with patch("crypto_swing_copilot.agents.strategy_agent.call_llm", return_value=mock_response):
            from crypto_swing_copilot.agents.strategy_agent import StrategyAgent
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

        with patch("crypto_swing_copilot.agents.strategy_agent.call_llm", return_value=mock_response):
            from crypto_swing_copilot.agents.strategy_agent import StrategyAgent
            agent = StrategyAgent()
            result = agent.run(sample_snapshot, sample_quant_signal, sample_sentiment_signal)

        # Must be overridden to SKIP, not SHORT
        assert result.direction == SignalDirection.NEUTRAL
        assert result.action == SetupAction.SKIP


# ---------------------------------------------------------------------------
# RiskAgent tests
# ---------------------------------------------------------------------------


class TestRiskAgent:
    def test_approve_valid_long(self, sample_proposal: SetupProposal) -> None:
        """High-confidence LONG → APPROVED, conviction=high, risk=2%."""
        with patch("crypto_swing_copilot.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Strong setup approved."}]'):
            from crypto_swing_copilot.agents.risk_agent import RiskAgent
            agent = RiskAgent()
            entries = agent.run([(sample_proposal, 0.82)])

        assert len(entries) == 1
        assert entries[0].verdict == PlaybookVerdict.APPROVED
        assert entries[0].conviction == "high"
        assert entries[0].suggested_risk_pct == 0.02
        assert entries[0].confidence_score == 0.82

    def test_approve_standard_conviction(self, sample_proposal: SetupProposal) -> None:
        """Mid-confidence LONG → APPROVED, conviction=standard, risk=1%."""
        with patch("crypto_swing_copilot.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Acceptable setup."}]'):
            from crypto_swing_copilot.agents.risk_agent import RiskAgent
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
            symbol="BTCUSDT", timeframe="4h",
            direction=SignalDirection.SHORT, action=SetupAction.SELL,
            entry_zone_low=62000, entry_zone_high=62500,
            stop_loss=63000, take_profit=60000,
            reward_risk_ratio=2.0, rationale="Bearish.",
        )

        with patch("crypto_swing_copilot.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Rejected."}]'):
            from crypto_swing_copilot.agents.risk_agent import RiskAgent
            agent = RiskAgent()
            entries = agent.run([(short_proposal, 0.85)])

        assert entries[0].verdict == PlaybookVerdict.REJECTED
        assert "Spot only" in entries[0].verdict_reasoning

    def test_reject_low_confidence(self, sample_proposal: SetupProposal) -> None:
        """Confidence below 0.65 threshold → REJECTED."""
        with patch("crypto_swing_copilot.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Low confidence."}]'):
            from crypto_swing_copilot.agents.risk_agent import RiskAgent
            agent = RiskAgent()
            entries = agent.run([(sample_proposal, 0.50)])

        assert entries[0].verdict == PlaybookVerdict.REJECTED
        assert "Confidence" in entries[0].verdict_reasoning
        assert entries[0].conviction == "none"
        assert entries[0].suggested_risk_pct == 0.0

    def test_reject_low_rr(self) -> None:
        """R:R below configured minimum (2.0) → REJECTED."""
        low_rr_proposal = SetupProposal(
            symbol="BTCUSDT", timeframe="4h",
            direction=SignalDirection.LONG, action=SetupAction.BUY,
            entry_zone_low=62000, entry_zone_high=62500,
            stop_loss=60500, take_profit=63500,
            reward_risk_ratio=1.5, rationale="Weak R:R.",
        )

        with patch("crypto_swing_copilot.agents.risk_agent.call_llm", return_value='[{"symbol": "BTCUSDT", "verdict_reasoning": "Low R:R."}]'):
            from crypto_swing_copilot.agents.risk_agent import RiskAgent
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
        from crypto_swing_copilot.agents.base import clean_json_response
        raw = '<think>Let me analyze this...</think>{"key": "value"}'
        assert clean_json_response(raw) == '{"key": "value"}'

    def test_strips_code_fences(self) -> None:
        from crypto_swing_copilot.agents.base import clean_json_response
        raw = '```json\n{"key": "value"}\n```'
        assert clean_json_response(raw) == '{"key": "value"}'

    def test_strips_both(self) -> None:
        from crypto_swing_copilot.agents.base import clean_json_response
        raw = '<think>thinking...</think>\n```json\n{"key": "value"}\n```'
        assert clean_json_response(raw) == '{"key": "value"}'

    def test_passthrough_clean_json(self) -> None:
        from crypto_swing_copilot.agents.base import clean_json_response
        raw = '{"key": "value"}'
        assert clean_json_response(raw) == '{"key": "value"}'


class TestParseJsonResponse:
    def test_valid_quant_signal(self) -> None:
        from crypto_swing_copilot.agents.base import parse_json_response
        raw = json.dumps({
            "symbol": "BTCUSDT", "timeframe": "4h",
            "trend_regime": "uptrend",
            "signals": ["Price above 200 EMA"],
            "confidence": 0.75, "reasoning": "Bullish.",
        })
        result = parse_json_response(raw, QuantSignal)
        assert result.confidence == 0.75

    def test_invalid_json_raises(self) -> None:
        from crypto_swing_copilot.agents.base import parse_json_response
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_json_response("not json at all", QuantSignal)

    def test_schema_mismatch_raises(self) -> None:
        from crypto_swing_copilot.agents.base import parse_json_response
        with pytest.raises(ValueError, match="Schema validation"):
            parse_json_response('{"wrong": "schema"}', QuantSignal)
