"""Unit tests for Phase 1.5 agents — mocked LLM calls, no real inference."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tenth_floor.data.models import (
    MacroRegime,
    MacroSignal,
    PairSnapshot,
    ReviewedSignal,
    ReviewVerdict,
    SentimentSnapshot,
    SetupAction,
    SignalDirection,
    TAIndicators,
    TradeProposal,
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
        support_levels=[61200.0, 61700.0],
        resistance_levels=[62800.0, 63500.0],
    )


@pytest.fixture
def sample_snapshot(sample_indicators: TAIndicators) -> PairSnapshot:
    return PairSnapshot(
        symbol="BTCUSDT",
        timeframe="1d",
        asset_class="crypto",
        current_price=62000.0,
        bar_timestamp=1710374400000,
        indicators=sample_indicators,
        sentiment=None,
        recent_closes=[62000, 61800, 61500, 61200, 61000],
        recent_volumes=[500, 480, 520, 510, 490],
    )


@pytest.fixture
def sample_sentiment() -> SentimentSnapshot:
    return SentimentSnapshot(
        fear_greed_value=65,
        fear_greed_label="Greed",
        fear_greed_trend=[65, 60, 55],
        headlines=[],
    )


@pytest.fixture
def sample_macro() -> MacroSignal:
    return MacroSignal(
        regime=MacroRegime.RISK_ON,
        regime_reasoning="Low VIX, moderate greed, stable dollar.",
        asset_class_impacts=[
            {"asset_class": "crypto", "outlook": "bullish", "reasoning": "Risk-on favours crypto"},
            {"asset_class": "equity", "outlook": "bullish", "reasoning": "Low VIX supports equities"},
            {"asset_class": "etf", "outlook": "neutral", "reasoning": "ETFs tracking broad market"},
            {"asset_class": "commodity", "outlook": "neutral", "reasoning": "Dollar stable"},
        ],
        alerts=[],
        vix_level=15.0,
        fear_greed_value=65,
        dxy_trend="stable",
    )


@pytest.fixture
def sample_proposal() -> TradeProposal:
    return TradeProposal(
        symbol="BTCUSDT",
        timeframe="1d",
        action=SetupAction.BUY,
        direction=SignalDirection.LONG,
        entry_zone_low=61800.0,
        entry_zone_high=62200.0,
        stop_loss=61000.0,
        take_profit=64000.0,
        confidence=0.82,
        rationale="Strong uptrend with EMA alignment.",
        entry_reasoning="Near EMA 20 support.",
        stop_reasoning="Below swing low at 61200.",
        target_reasoning="Resistance at 64000.",
        confluence_factors=["EMA alignment", "Volume support"],
        risk_factors=["Overhead resistance"],
    )


# ---------------------------------------------------------------------------
# MacroAnalyst tests
# ---------------------------------------------------------------------------


class TestMacroAnalyst:
    def test_run_returns_macro_signal(self, sample_sentiment: SentimentSnapshot) -> None:
        mock_response = json.dumps({
            "regime": "risk_on",
            "regime_reasoning": "Low VIX, greed sentiment.",
            "asset_class_impacts": [
                {"asset_class": "crypto", "outlook": "bullish", "reasoning": "Risk on."},
            ],
            "alerts": [],
            "vix_level": 15.0,
            "fear_greed_value": 65,
            "dxy_trend": "stable",
        })

        with patch("tenth_floor.agents.macro_analyst.call_llm", return_value=mock_response):
            from tenth_floor.agents.macro_analyst import MacroAnalyst
            agent = MacroAnalyst()
            result = agent.run(sample_sentiment)

        assert isinstance(result, MacroSignal)
        assert result.regime == MacroRegime.RISK_ON
        assert result.fear_greed_value == 65
        assert result.dxy_trend == "stable"

    def test_prompt_contains_fg_value(self, sample_sentiment: SentimentSnapshot) -> None:
        from tenth_floor.agents.macro_analyst import MacroAnalyst
        prompt = MacroAnalyst._build_prompt(sample_sentiment, None, None)
        assert "65" in prompt
        assert "Greed" in prompt

    def test_prompt_includes_vix_data(self, sample_sentiment: SentimentSnapshot) -> None:
        from tenth_floor.agents.macro_analyst import MacroAnalyst
        vix = {"level": 22.5, "change_pct": 3.2, "trend": "rising"}
        prompt = MacroAnalyst._build_prompt(sample_sentiment, vix, None)
        assert "22.5" in prompt
        assert "rising" in prompt

    def test_prompt_includes_dxy_data(self, sample_sentiment: SentimentSnapshot) -> None:
        from tenth_floor.agents.macro_analyst import MacroAnalyst
        dxy = {"level": 104.5, "change_pct": -0.3, "trend": "weakening"}
        prompt = MacroAnalyst._build_prompt(sample_sentiment, None, dxy)
        assert "104.5" in prompt
        assert "weakening" in prompt


# ---------------------------------------------------------------------------
# TradeAnalyst tests
# ---------------------------------------------------------------------------


class TestTradeAnalyst:
    def test_run_returns_trade_proposal(
        self, sample_snapshot: PairSnapshot, sample_macro: MacroSignal,
    ) -> None:
        mock_response = json.dumps({
            "symbol": "BTCUSDT",
            "timeframe": "1d",
            "action": "buy",
            "direction": "long",
            "entry_zone_low": 61800.0,
            "entry_zone_high": 62200.0,
            "stop_loss": 61000.0,
            "take_profit": 64000.0,
            "confidence": 0.78,
            "rationale": "Strong setup.",
            "entry_reasoning": "Near EMA 20.",
            "stop_reasoning": "Below swing low.",
            "target_reasoning": "Resistance at 64000.",
            "confluence_factors": ["EMA alignment"],
            "risk_factors": [],
        })

        with patch("tenth_floor.agents.trade_analyst.call_llm", return_value=mock_response):
            from tenth_floor.agents.trade_analyst import TradeAnalyst
            agent = TradeAnalyst()
            result = agent.run(sample_snapshot, sample_macro)

        assert isinstance(result, TradeProposal)
        assert result.action == SetupAction.BUY
        assert result.direction == SignalDirection.LONG
        assert result.confidence == 0.78

    def test_short_override_to_skip(
        self, sample_snapshot: PairSnapshot, sample_macro: MacroSignal,
    ) -> None:
        """If LLM returns SHORT direction, TradeAnalyst must override to SKIP."""
        mock_response = json.dumps({
            "symbol": "BTCUSDT",
            "timeframe": "1d",
            "action": "buy",
            "direction": "short",
            "entry_zone_low": 62000.0,
            "entry_zone_high": 62500.0,
            "stop_loss": 61000.0,
            "take_profit": 64000.0,
            "confidence": 0.70,
            "rationale": "Bearish signal.",
            "confluence_factors": [],
            "risk_factors": [],
        })

        with patch("tenth_floor.agents.trade_analyst.call_llm", return_value=mock_response):
            from tenth_floor.agents.trade_analyst import TradeAnalyst
            agent = TradeAnalyst()
            result = agent.run(sample_snapshot, sample_macro)

        assert result.direction == SignalDirection.NEUTRAL
        assert result.action == SetupAction.SKIP

    def test_symbol_override(
        self, sample_snapshot: PairSnapshot, sample_macro: MacroSignal,
    ) -> None:
        """LLM symbol output is overridden with snapshot's authoritative symbol."""
        mock_response = json.dumps({
            "symbol": "btcusdt",
            "timeframe": "1d",
            "action": "buy",
            "direction": "long",
            "entry_zone_low": 61800.0,
            "entry_zone_high": 62200.0,
            "stop_loss": 61000.0,
            "take_profit": 64000.0,
            "confidence": 0.78,
            "rationale": "Strong setup.",
            "confluence_factors": [],
            "risk_factors": [],
        })

        with patch("tenth_floor.agents.trade_analyst.call_llm", return_value=mock_response):
            from tenth_floor.agents.trade_analyst import TradeAnalyst
            agent = TradeAnalyst()
            result = agent.run(sample_snapshot, sample_macro)

        assert result.symbol == "BTCUSDT"

    def test_prompt_contains_indicators(
        self, sample_snapshot: PairSnapshot, sample_macro: MacroSignal,
    ) -> None:
        from tenth_floor.agents.trade_analyst import TradeAnalyst
        prompt = TradeAnalyst._build_prompt(sample_snapshot, sample_macro)
        assert "62000.0" in prompt  # EMA 20
        assert "RSI 14: 55.0" in prompt
        assert "BTCUSDT" in prompt
        assert "risk_on" in prompt
        # Asset class is included and correctly matched
        assert "ASSET CLASS: crypto" in prompt
        assert "bullish: Risk-on favours crypto" in prompt


# ---------------------------------------------------------------------------
# RiskReviewer tests
# ---------------------------------------------------------------------------


class TestRiskReviewer:
    def test_run_returns_reviewed_signals(
        self, sample_proposal: TradeProposal, sample_macro: MacroSignal,
    ) -> None:
        mock_response = json.dumps({
            "symbol": "BTCUSDT",
            "verdict": "approve",
            "conviction": "high",
            "reasoning": "Strongest setup today.",
            "risk_notes": "Watch overhead resistance.",
        })

        with patch("tenth_floor.agents.risk_reviewer.call_llm", return_value=mock_response):
            from tenth_floor.agents.risk_reviewer import RiskReviewer
            reviewer = RiskReviewer()
            reviewed = reviewer.run([sample_proposal], sample_macro)

        assert len(reviewed) == 1
        assert isinstance(reviewed[0], ReviewedSignal)
        assert reviewed[0].verdict == ReviewVerdict.APPROVE
        assert reviewed[0].conviction == "high"

    def test_reject_verdict(
        self, sample_proposal: TradeProposal, sample_macro: MacroSignal,
    ) -> None:
        mock_response = json.dumps({
            "symbol": "BTCUSDT",
            "verdict": "reject",
            "conviction": "standard",
            "reasoning": "Macro headwind, risk-off regime.",
            "risk_notes": "VIX elevated.",
        })

        with patch("tenth_floor.agents.risk_reviewer.call_llm", return_value=mock_response):
            from tenth_floor.agents.risk_reviewer import RiskReviewer
            reviewer = RiskReviewer()
            reviewed = reviewer.run([sample_proposal], sample_macro)

        assert reviewed[0].verdict == ReviewVerdict.REJECT

    def test_empty_proposals_returns_empty(self, sample_macro: MacroSignal) -> None:
        from tenth_floor.agents.risk_reviewer import RiskReviewer
        with patch("tenth_floor.agents.risk_reviewer.load_agent_config", return_value={}), \
             patch("tenth_floor.agents.risk_reviewer.load_risk_profile", return_value={}):
            reviewer = RiskReviewer()
        assert reviewer.run([], sample_macro) == []

    def test_parse_error_defaults_to_reject(
        self, sample_proposal: TradeProposal, sample_macro: MacroSignal,
    ) -> None:
        """If LLM response is unparseable, default to REJECT for safety."""
        with patch("tenth_floor.agents.risk_reviewer.call_llm", return_value="not json"):
            from tenth_floor.agents.risk_reviewer import RiskReviewer
            reviewer = RiskReviewer()
            reviewed = reviewer.run([sample_proposal], sample_macro)

        assert len(reviewed) == 1
        assert reviewed[0].verdict == ReviewVerdict.REJECT
        assert reviewed[0].conviction == "standard"

    def test_wrong_symbol_is_overridden(
        self, sample_proposal: TradeProposal, sample_macro: MacroSignal,
    ) -> None:
        """LLM symbol echo is never trusted — proposal symbol always wins."""
        mock_response = json.dumps({
            "symbol": "ETHUSDT",
            "verdict": "approve",
            "conviction": "high",
            "reasoning": "Wrong symbol echoed.",
            "risk_notes": "",
        })

        with patch("tenth_floor.agents.risk_reviewer.call_llm", return_value=mock_response):
            from tenth_floor.agents.risk_reviewer import RiskReviewer
            reviewer = RiskReviewer()
            reviewed = reviewer.run([sample_proposal], sample_macro)

        assert len(reviewed) == 1
        assert reviewed[0].symbol == "BTCUSDT"
        assert reviewed[0].verdict == ReviewVerdict.APPROVE

    def test_per_proposal_loop_calls_llm_per_proposal(
        self, sample_proposal: TradeProposal, sample_macro: MacroSignal,
    ) -> None:
        """N proposals → N LLM calls. Each call is independent."""
        # Two proposals, second is a copy with a different symbol
        second = sample_proposal.model_copy(update={"symbol": "ETHUSDT"})
        proposals = [sample_proposal, second]

        responses = [
            json.dumps({
                "symbol": "BTCUSDT", "verdict": "approve", "conviction": "high",
                "reasoning": "Top pick.", "risk_notes": "",
            }),
            json.dumps({
                "symbol": "ETHUSDT", "verdict": "reject", "conviction": "standard",
                "reasoning": "Already exposed via BTC.", "risk_notes": "",
            }),
        ]
        mock = MagicMock(side_effect=responses)
        with patch("tenth_floor.agents.risk_reviewer.call_llm", mock):
            from tenth_floor.agents.risk_reviewer import RiskReviewer
            reviewer = RiskReviewer()
            reviewed = reviewer.run(proposals, sample_macro)

        assert mock.call_count == 2
        # Returned in input order
        assert [r.symbol for r in reviewed] == ["BTCUSDT", "ETHUSDT"]
        assert reviewed[0].verdict == ReviewVerdict.APPROVE
        assert reviewed[1].verdict == ReviewVerdict.REJECT


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_proposal(self, sample_proposal: TradeProposal) -> None:
        from tenth_floor.validation import validate_proposal
        result = validate_proposal(sample_proposal)
        assert result.valid is True
        assert result.reward_risk_ratio >= 1.5

    def test_skip_always_valid(self) -> None:
        from tenth_floor.validation import validate_proposal
        skip = TradeProposal(
            symbol="BTCUSDT", timeframe="1d",
            action=SetupAction.SKIP, direction=SignalDirection.NEUTRAL,
            entry_zone_low=100.0, entry_zone_high=101.0,
            stop_loss=95.0, take_profit=110.0,
            confidence=0.2, rationale="No edge.",
        )
        result = validate_proposal(skip)
        assert result.valid is True

    def test_reject_short(self) -> None:
        from tenth_floor.validation import validate_proposal
        short = TradeProposal(
            symbol="BTCUSDT", timeframe="1d",
            action=SetupAction.BUY, direction=SignalDirection.SHORT,
            entry_zone_low=62000.0, entry_zone_high=62500.0,
            stop_loss=63000.0, take_profit=60000.0,
            confidence=0.7, rationale="Bearish.",
        )
        result = validate_proposal(short)
        assert result.valid is False
        assert "SHORT" in result.reason

    def test_reject_sl_above_entry(self) -> None:
        from tenth_floor.validation import validate_proposal
        bad = TradeProposal(
            symbol="BTCUSDT", timeframe="1d",
            action=SetupAction.BUY, direction=SignalDirection.LONG,
            entry_zone_low=62000.0, entry_zone_high=62500.0,
            stop_loss=63000.0, take_profit=66000.0,
            confidence=0.7, rationale="Bad SL.",
        )
        result = validate_proposal(bad)
        assert result.valid is False
        assert "SL" in result.reason

    def test_reject_tp_below_entry(self) -> None:
        from tenth_floor.validation import validate_proposal
        bad = TradeProposal(
            symbol="BTCUSDT", timeframe="1d",
            action=SetupAction.BUY, direction=SignalDirection.LONG,
            entry_zone_low=62000.0, entry_zone_high=62500.0,
            stop_loss=61000.0, take_profit=62000.0,
            confidence=0.7, rationale="Bad TP.",
        )
        result = validate_proposal(bad)
        assert result.valid is False
        assert "TP" in result.reason

    def test_reject_low_rr(self) -> None:
        from tenth_floor.validation import validate_proposal
        # R:R = (63000 - 62250) / (62250 - 62000) = 750/250 = 3.0 — wait, need low R:R
        # entry_mid = 62250, risk = 62250-62100 = 150, reward = 62400-62250 = 150 → R:R = 1.0
        low_rr = TradeProposal(
            symbol="BTCUSDT", timeframe="1d",
            action=SetupAction.BUY, direction=SignalDirection.LONG,
            entry_zone_low=62000.0, entry_zone_high=62500.0,
            stop_loss=62000.0 - 1.0, take_profit=62500.0 + 1.0,
            confidence=0.7, rationale="Low R:R.",
        )
        result = validate_proposal(low_rr)
        assert result.valid is False
        assert "R:R" in result.reason

    def test_reject_sl_too_far(self) -> None:
        from tenth_floor.validation import validate_proposal
        # entry_mid = 62250, SL at 50000 → 19.7% below → exceeds 15%
        far_sl = TradeProposal(
            symbol="BTCUSDT", timeframe="1d",
            action=SetupAction.BUY, direction=SignalDirection.LONG,
            entry_zone_low=62000.0, entry_zone_high=62500.0,
            stop_loss=50000.0, take_profit=90000.0,
            confidence=0.7, rationale="SL too far.",
        )
        result = validate_proposal(far_sl)
        assert result.valid is False
        assert "15%" in result.reason

    def test_reject_entry_zone_inverted(self) -> None:
        from tenth_floor.validation import validate_proposal
        inverted = TradeProposal(
            symbol="BTCUSDT", timeframe="1d",
            action=SetupAction.BUY, direction=SignalDirection.LONG,
            entry_zone_low=62500.0, entry_zone_high=62000.0,
            stop_loss=61000.0, take_profit=65000.0,
            confidence=0.7, rationale="Inverted zone.",
        )
        result = validate_proposal(inverted)
        assert result.valid is False
        assert "entry_zone_low" in result.reason


# ---------------------------------------------------------------------------
# base.py utility tests
# ---------------------------------------------------------------------------


class TestCleanJsonResponse:
    def test_strips_think_tags(self) -> None:
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
    def test_valid_macro_signal(self) -> None:
        from tenth_floor.agents.base import parse_json_response
        raw = json.dumps({
            "regime": "risk_on",
            "regime_reasoning": "Low VIX.",
            "asset_class_impacts": [],
            "alerts": [],
            "vix_level": 15.0,
            "fear_greed_value": 65,
            "dxy_trend": "stable",
        })
        result = parse_json_response(raw, MacroSignal)
        assert result.regime == MacroRegime.RISK_ON

    def test_invalid_json_raises(self) -> None:
        from tenth_floor.agents.base import parse_json_response
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_json_response("not json at all", MacroSignal)

    def test_schema_mismatch_raises(self) -> None:
        from tenth_floor.agents.base import parse_json_response
        with pytest.raises(ValueError, match="Schema validation"):
            parse_json_response('{"wrong": "schema"}', MacroSignal)
