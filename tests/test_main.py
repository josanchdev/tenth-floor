"""Tests for the daily pipeline orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

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
from crypto_swing_copilot.main import run_pipeline

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_ohlcv_df(rows: int = 50) -> pd.DataFrame:
    """Minimal OHLCV DataFrame."""
    return pd.DataFrame({
        "timestamp": [1_700_000_000_000 + i * 14_400_000 for i in range(rows)],
        "open": [100.0] * rows,
        "high": [105.0] * rows,
        "low": [95.0] * rows,
        "close": [102.0] * rows,
        "volume": [1000.0] * rows,
    })


def _make_snapshot() -> PairSnapshot:
    return PairSnapshot(
        symbol="BTCUSDT",
        timeframe="1d",
        current_price=62_000.0,
        bar_timestamp=1_700_000_000_000,
        indicators=TAIndicators(),
        sentiment=None,
        recent_closes=[62_000.0],
        recent_volumes=[1000.0],
    )


def _make_quant_signal() -> QuantSignal:
    return QuantSignal(
        symbol="BTCUSDT",
        timeframe="1d",
        trend_regime=TrendRegime.UPTREND,
        signals=["EMA bullish"],
        confidence=0.82,
        reasoning="Strong uptrend",
    )


def _make_sentiment_signal() -> SentimentSignal:
    return SentimentSignal(
        bias=SentimentBias.GREED,
        risk_narrative="Market is greedy",
        key_headlines=["BTC surges"],
        fear_greed_value=72,
    )


def _make_proposal() -> SetupProposal:
    return SetupProposal(
        symbol="BTCUSDT",
        timeframe="1d",
        direction=SignalDirection.LONG,
        action=SetupAction.BUY,
        entry_zone_low=62_000.0,
        entry_zone_high=63_000.0,
        stop_loss=60_500.0,
        take_profit=66_000.0,
        reward_risk_ratio=2.0,
        rationale="EMA golden cross",
        confluence_factors=["RSI bounce"],
    )


def _make_entry(**overrides) -> PlaybookEntry:
    defaults = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "report_date": "2026-03-20",
        "verdict": PlaybookVerdict.APPROVED,
        "verdict_reasoning": "Meets threshold",
        "direction": SignalDirection.LONG,
        "action": SetupAction.BUY,
        "entry_zone_low": 62_000.0,
        "entry_zone_high": 63_000.0,
        "stop_loss": 60_500.0,
        "take_profit": 66_000.0,
        "reward_risk_ratio": 2.0,
        "confidence_score": 0.82,
        "conviction": "high",
        "suggested_risk_pct": 0.02,
        "strategy_rationale": "EMA golden cross",
        "rank": 1,
    }
    defaults.update(overrides)
    return PlaybookEntry(**defaults)


# ---------------------------------------------------------------------------
# Patches
# ---------------------------------------------------------------------------

_PATCH_BASE = "crypto_swing_copilot.main"


def _build_patches(
    *,
    snapshots=None,
    quant_signal=None,
    sentiment_signal=None,
    proposal=None,
    entries=None,
):
    """Return a dict of mock targets and their return values."""
    if snapshots is None:
        snapshots = [_make_snapshot()]
    if quant_signal is None:
        quant_signal = _make_quant_signal()
    if sentiment_signal is None:
        sentiment_signal = _make_sentiment_signal()
    if proposal is None:
        proposal = _make_proposal()
    if entries is None:
        entries = [_make_entry()]

    mock_fetcher = MagicMock()
    mock_fetcher.fetch_universe.return_value = {"BTCUSDT": {"4h": _make_ohlcv_df()}}

    mock_sentiment = MagicMock()
    mock_sentiment.fetch_snapshot.return_value = SentimentSnapshot(
        fear_greed_value=72,
        fear_greed_label="Greed",
    )

    mock_builder = MagicMock()
    mock_builder.build_universe.return_value = snapshots

    mock_quant = MagicMock()
    mock_quant.run.return_value = quant_signal

    mock_sentiment_agent = MagicMock()
    mock_sentiment_agent.run.return_value = sentiment_signal

    mock_strategy = MagicMock()
    mock_strategy.run.return_value = proposal

    mock_risk = MagicMock()
    mock_risk.run.return_value = entries

    mock_signal_logger = MagicMock()
    mock_signal_logger.log.return_value = "BTCUSDT_2026-03-20_abc12345"
    mock_signal_logger.open_signal_count.return_value = 3
    mock_signal_logger.__enter__ = MagicMock(return_value=mock_signal_logger)
    mock_signal_logger.__exit__ = MagicMock(return_value=False)

    mock_notifier = MagicMock()
    mock_notifier.post.return_value = True

    return {
        f"{_PATCH_BASE}.MarketDataFetcher": MagicMock(return_value=mock_fetcher),
        f"{_PATCH_BASE}.SentimentFetcher": MagicMock(return_value=mock_sentiment),
        f"{_PATCH_BASE}.SnapshotBuilder": MagicMock(return_value=mock_builder),
        f"{_PATCH_BASE}.QuantAgent": MagicMock(return_value=mock_quant),
        f"{_PATCH_BASE}.SentimentAgent": MagicMock(return_value=mock_sentiment_agent),
        f"{_PATCH_BASE}.StrategyAgent": MagicMock(return_value=mock_strategy),
        f"{_PATCH_BASE}.RiskAgent": MagicMock(return_value=mock_risk),
        f"{_PATCH_BASE}.SignalLogger": MagicMock(return_value=mock_signal_logger),
        f"{_PATCH_BASE}.DiscordNotifier": MagicMock(return_value=mock_notifier),
    }, {
        "fetcher": mock_fetcher,
        "sentiment_fetcher": mock_sentiment,
        "builder": mock_builder,
        "quant": mock_quant,
        "sentiment_agent": mock_sentiment_agent,
        "strategy": mock_strategy,
        "risk": mock_risk,
        "signal_logger": mock_signal_logger,
        "notifier": mock_notifier,
    }


def _run_with_patches(*, dry_run=False, **kwargs):
    """Run pipeline with all components mocked. Returns (entries, mocks)."""
    patches, mocks = _build_patches(**kwargs)
    ctx_managers = [patch(target, val) for target, val in patches.items()]
    for cm in ctx_managers:
        cm.start()
    try:
        entries = run_pipeline(dry_run=dry_run)
    finally:
        for cm in ctx_managers:
            cm.stop()
    return entries, mocks


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPipelineFlow:
    """Test the end-to-end pipeline wiring."""

    def test_full_pipeline_returns_entries(self):
        entries, mocks = _run_with_patches()
        assert len(entries) == 1
        assert entries[0].verdict == PlaybookVerdict.APPROVED

    def test_calls_all_agents_in_order(self):
        _, mocks = _run_with_patches()

        mocks["sentiment_agent"].run.assert_called_once()
        mocks["quant"].run.assert_called_once()
        mocks["strategy"].run.assert_called_once()
        mocks["risk"].run.assert_called_once()

    def test_logs_approved_signals_to_db(self):
        _, mocks = _run_with_patches()

        mocks["signal_logger"].log.assert_called_once()
        mocks["signal_logger"].open_signal_count.assert_called_once()

    def test_posts_to_discord(self):
        _, mocks = _run_with_patches()

        mocks["notifier"].post.assert_called_once()
        call_kwargs = mocks["notifier"].post.call_args
        assert call_kwargs.kwargs["open_count"] == 3

    def test_dry_run_skips_db_and_discord(self):
        entries, mocks = _run_with_patches(dry_run=True)

        assert len(entries) == 1
        mocks["signal_logger"].log.assert_not_called()
        mocks["notifier"].post.assert_not_called()

    def test_empty_snapshots_returns_empty(self):
        entries, _ = _run_with_patches(snapshots=[])
        assert entries == []

    def test_rejected_entries_not_logged_to_db(self):
        rejected = _make_entry(verdict=PlaybookVerdict.REJECTED)
        entries, mocks = _run_with_patches(entries=[rejected])

        # SignalLogger.log is called but should skip rejected
        # (the actual filtering is in SignalLogger, not main.py)
        # main.py only passes approved entries to log
        mocks["signal_logger"].log.assert_not_called()

    def test_agent_failure_skips_pair(self):
        """If QuantAgent raises for one pair, pipeline continues."""
        snap1 = _make_snapshot()
        snap2 = PairSnapshot(
            symbol="ETHUSDT",
            timeframe="1d",
            current_price=3_000.0,
            bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(),
            sentiment=None,
            recent_closes=[3_000.0],
            recent_volumes=[500.0],
        )

        mock_quant = MagicMock()
        mock_quant.run.side_effect = [_make_quant_signal(), RuntimeError("LLM timeout")]

        patches, mocks = _build_patches(snapshots=[snap1, snap2])
        # Override quant mock to have side_effect
        patches[f"{_PATCH_BASE}.QuantAgent"] = MagicMock(return_value=mock_quant)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_pipeline()
        finally:
            for cm in ctx_managers:
                cm.stop()

        # Pipeline still completes — one proposal succeeds, one fails
        assert mock_quant.run.call_count == 2


class TestCLI:
    """Test CLI argument parsing."""

    def test_parse_args_defaults(self):
        from crypto_swing_copilot.main import _parse_args

        args = _parse_args([])
        assert args.pairs is None
        assert args.dry_run is False
        assert args.log_level == "INFO"

    def test_parse_args_with_pairs(self):
        from crypto_swing_copilot.main import _parse_args

        args = _parse_args(["BTCUSDT", "ETHUSDT"])
        assert args.pairs == ["BTCUSDT", "ETHUSDT"]

    def test_parse_args_dry_run(self):
        from crypto_swing_copilot.main import _parse_args

        args = _parse_args(["--dry-run"])
        assert args.dry_run is True
        assert args.pairs is None


class TestVolumeConfirmationGate:
    """Volume gate rejects low-volume contrarian buys in downtrends."""

    def test_low_volume_downtrend_buy_skipped(self):
        """BUY in downtrend with volume_ratio < 1.3 should be skipped."""
        # Snapshot with low volume ratio
        snap = PairSnapshot(
            symbol="BTCUSDT", timeframe="1d", current_price=62000.0,
            bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(volume_ratio=0.8),
            recent_closes=[62000.0], recent_volumes=[1000.0],
        )

        downtrend_quant = QuantSignal(
            symbol="BTCUSDT", timeframe="1d",
            trend_regime=TrendRegime.DOWNTREND,
            signals=["Price below 200 EMA"],
            confidence=0.70,
            reasoning="Downtrend",
        )

        buy_proposal = _make_proposal()

        mock_quant = MagicMock()
        mock_quant.run.return_value = downtrend_quant

        mock_strategy = MagicMock()
        mock_strategy.run.return_value = buy_proposal

        patches, mocks = _build_patches(
            snapshots=[snap],
            quant_signal=downtrend_quant,
            proposal=buy_proposal,
        )
        patches[f"{_PATCH_BASE}.QuantAgent"] = MagicMock(return_value=mock_quant)
        patches[f"{_PATCH_BASE}.StrategyAgent"] = MagicMock(return_value=mock_strategy)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_pipeline(dry_run=True)
        finally:
            for cm in ctx_managers:
                cm.stop()

        # Strategy was called (downtrend, not strong_downtrend)
        mock_strategy.run.assert_called_once()
        # But RiskAgent should NOT be called — volume gate killed the proposal
        mocks["risk"].run.assert_not_called()

    def test_high_volume_downtrend_buy_passes(self):
        """BUY in downtrend with volume_ratio >= 1.3 should pass through."""
        snap = PairSnapshot(
            symbol="BTCUSDT", timeframe="1d", current_price=62000.0,
            bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(volume_ratio=1.8),
            recent_closes=[62000.0], recent_volumes=[1000.0],
        )

        downtrend_quant = QuantSignal(
            symbol="BTCUSDT", timeframe="1d",
            trend_regime=TrendRegime.DOWNTREND,
            signals=["Price below 200 EMA"],
            confidence=0.70,
            reasoning="Downtrend but with volume",
        )

        patches, mocks = _build_patches(
            snapshots=[snap],
            quant_signal=downtrend_quant,
        )

        mock_quant = MagicMock()
        mock_quant.run.return_value = downtrend_quant
        patches[f"{_PATCH_BASE}.QuantAgent"] = MagicMock(return_value=mock_quant)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_pipeline(dry_run=True)
        finally:
            for cm in ctx_managers:
                cm.stop()

        # With high volume, proposal passes through to RiskAgent
        mocks["risk"].run.assert_called_once()


class TestTrendRegimeGate:
    """Trend-regime gate skips strong_downtrend snapshots before StrategyAgent."""

    def test_strong_downtrend_skips_strategy(self):
        """QuantAgent returning strong_downtrend should prevent StrategyAgent call."""
        downtrend_quant = QuantSignal(
            symbol="BTCUSDT",
            timeframe="1d",
            trend_regime=TrendRegime.STRONG_DOWNTREND,
            signals=["EMA death cross (20 < 50)", "Price below 200 EMA"],
            confidence=0.45,
            reasoning="Strong downtrend — death cross accelerating",
        )

        patches, mocks = _build_patches(quant_signal=downtrend_quant)
        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_pipeline(dry_run=True)
        finally:
            for cm in ctx_managers:
                cm.stop()

        # QuantAgent was called, but StrategyAgent should NOT be called
        mocks["quant"].run.assert_called_once()
        mocks["strategy"].run.assert_not_called()

    def test_downtrend_still_allows_strategy(self):
        """Regular downtrend (not strong) should still reach StrategyAgent."""
        downtrend_quant = QuantSignal(
            symbol="BTCUSDT",
            timeframe="1d",
            trend_regime=TrendRegime.DOWNTREND,
            signals=["Price below 200 EMA"],
            confidence=0.55,
            reasoning="Downtrend but not extreme",
        )

        patches, mocks = _build_patches(quant_signal=downtrend_quant)
        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_pipeline(dry_run=True)
        finally:
            for cm in ctx_managers:
                cm.stop()

        mocks["strategy"].run.assert_called_once()


class TestRelativeStrengthGate:
    """Skip alts underperforming BTC in fear environments."""

    def test_weak_alt_skipped_in_fear(self):
        """Alt down more than BTC + fear sentiment → skip."""
        # BTC down 5% over recent closes (trend_score high enough to pass pre-filter)
        btc_snap = PairSnapshot(
            symbol="BTCUSDT", timeframe="1d",
            current_price=57_000.0, bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(trend_score=0.71),
            recent_closes=[57_000.0, 58_000.0, 59_000.0, 60_000.0],  # -5%
            recent_volumes=[1000.0] * 4,
        )
        # ALT down 10% — underperforming BTC
        alt_snap = PairSnapshot(
            symbol="SOLUSDT", timeframe="1d",
            current_price=90.0, bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(trend_score=0.71),
            recent_closes=[90.0, 94.0, 97.0, 100.0],  # -10%
            recent_volumes=[500.0] * 4,
        )

        quant_signal = QuantSignal(
            symbol="BTCUSDT", timeframe="1d",
            trend_regime=TrendRegime.UPTREND,
            signals=["EMA bullish"], confidence=0.75, reasoning="Up",
        )
        fear_sentiment = SentimentSignal(
            bias=SentimentBias.FEAR,
            risk_narrative="Fear", key_headlines=[], fear_greed_value=25,
        )
        btc_skip = SetupProposal(
            symbol="BTCUSDT", timeframe="1d",
            direction=SignalDirection.NEUTRAL, action=SetupAction.SKIP,
            entry_zone_low=56_000.0, entry_zone_high=58_000.0,
            stop_loss=54_000.0, take_profit=62_000.0,
            reward_risk_ratio=2.0, rationale="Skip BTC", confluence_factors=[],
        )
        sol_buy = SetupProposal(
            symbol="SOLUSDT", timeframe="1d",
            direction=SignalDirection.LONG, action=SetupAction.BUY,
            entry_zone_low=89.0, entry_zone_high=91.0,
            stop_loss=85.0, take_profit=100.0,
            reward_risk_ratio=2.5, rationale="Buy SOL", confluence_factors=[],
        )

        mock_strategy = MagicMock()
        mock_strategy.run.side_effect = [btc_skip, sol_buy]

        mock_risk = MagicMock()
        mock_risk.run.return_value = []

        patches, mocks = _build_patches(
            snapshots=[btc_snap, alt_snap],
            quant_signal=quant_signal,
            sentiment_signal=fear_sentiment,
        )
        patches[f"{_PATCH_BASE}.StrategyAgent"] = MagicMock(return_value=mock_strategy)
        patches[f"{_PATCH_BASE}.RiskAgent"] = MagicMock(return_value=mock_risk)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_pipeline(dry_run=True)
        finally:
            for cm in ctx_managers:
                cm.stop()

        # BTC is SKIP (dropped at strategy gate), SOL is RS-gated.
        # No proposals reach RiskAgent at all.
        if mock_risk.run.call_args is not None:
            risk_args = mock_risk.run.call_args[0][0]
            symbols = [p.symbol for p, _ in risk_args]
            assert "SOLUSDT" not in symbols, (
                f"SOLUSDT should have been RS-gated, got: {symbols}"
            )
        # else: RiskAgent never called — both pairs correctly filtered

    def test_strong_alt_passes_in_fear(self):
        """Alt outperforming BTC in fear → passes."""
        # BTC down 8% (trend_score high enough to pass pre-filter)
        btc_snap = PairSnapshot(
            symbol="BTCUSDT", timeframe="1d",
            current_price=55_200.0, bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(trend_score=0.71),
            recent_closes=[55_200.0, 57_000.0, 59_000.0, 60_000.0],  # -8%
            recent_volumes=[1000.0] * 4,
        )
        # ALT down only 3% — outperforming BTC (relative strength)
        alt_snap = PairSnapshot(
            symbol="SOLUSDT", timeframe="1d",
            current_price=97.0, bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(trend_score=0.71),
            recent_closes=[97.0, 98.0, 99.0, 100.0],  # -3%
            recent_volumes=[500.0] * 4,
        )

        fear_sentiment = SentimentSignal(
            bias=SentimentBias.EXTREME_FEAR,
            risk_narrative="Extreme fear", key_headlines=[], fear_greed_value=10,
        )

        entries, mocks = _run_with_patches(
            dry_run=True,
            snapshots=[btc_snap, alt_snap],
            sentiment_signal=fear_sentiment,
        )
        # Strategy should be called for both — alt not gated
        assert mocks["strategy"].run.call_count == 2

    def test_gate_inactive_in_greed(self):
        """RS gate only triggers in fear — greed environments pass all alts."""
        btc_snap = PairSnapshot(
            symbol="BTCUSDT", timeframe="1d",
            current_price=62_000.0, bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(trend_score=0.86),
            recent_closes=[62_000.0, 61_000.0, 60_000.0],
            recent_volumes=[1000.0] * 3,
        )
        # Alt underperforming, but sentiment is greed — gate should NOT trigger
        alt_snap = PairSnapshot(
            symbol="SOLUSDT", timeframe="1d",
            current_price=95.0, bar_timestamp=1_700_000_000_000,
            indicators=TAIndicators(trend_score=0.71),
            recent_closes=[95.0, 98.0, 100.0],  # -5% while BTC +3%
            recent_volumes=[500.0] * 3,
        )

        greed_sentiment = SentimentSignal(
            bias=SentimentBias.GREED,
            risk_narrative="Greed", key_headlines=[], fear_greed_value=72,
        )

        entries, mocks = _run_with_patches(
            dry_run=True,
            snapshots=[btc_snap, alt_snap],
            sentiment_signal=greed_sentiment,
        )
        assert mocks["strategy"].run.call_count == 2


class TestBTCCorrelationGuard:
    """BTC-correlation guard limits alt signals when BTC is not approved."""

    def test_btc_skip_caps_alts_to_2(self):
        """When BTC proposal is skip, cap alt signals to 2.

        run_pipeline() returns *all* RiskAgent entries (approved + rejected),
        but the BTC-correlation guard filters the approved subset before
        logging/posting.  We verify via SignalLogger.log call count.
        """
        btc_snap = PairSnapshot(
            symbol="BTCUSDT", timeframe="1d", current_price=62000.0,
            bar_timestamp=1_700_000_000_000, indicators=TAIndicators(),
            recent_closes=[62000.0], recent_volumes=[1000.0],
        )
        alt_snaps = [
            PairSnapshot(
                symbol=sym, timeframe="1d", current_price=100.0,
                bar_timestamp=1_700_000_000_000, indicators=TAIndicators(),
                recent_closes=[100.0], recent_volumes=[500.0],
            )
            for sym in ["ETHUSDT", "SOLUSDT", "ADAUSDT", "DOTUSDT"]
        ]

        btc_proposal = SetupProposal(
            symbol="BTCUSDT", timeframe="1d",
            direction=SignalDirection.NEUTRAL, action=SetupAction.SKIP,
            entry_zone_low=61000.0, entry_zone_high=62000.0,
            stop_loss=59000.0, take_profit=66000.0,
            reward_risk_ratio=2.0, rationale="No edge", confluence_factors=[],
        )

        mock_quant = MagicMock()
        mock_quant.run.return_value = _make_quant_signal()

        mock_strategy = MagicMock()
        mock_strategy.run.side_effect = [btc_proposal] + [
            SetupProposal(
                symbol=sym, timeframe="1d",
                direction=SignalDirection.LONG, action=SetupAction.BUY,
                entry_zone_low=90.0, entry_zone_high=100.0,
                stop_loss=85.0, take_profit=115.0,
                reward_risk_ratio=2.0, rationale="Alt buy",
                confluence_factors=["EMA support"],
            )
            for sym in ["ETHUSDT", "SOLUSDT", "ADAUSDT", "DOTUSDT"]
        ]

        alt_entries = [
            _make_entry(
                symbol=sym, timeframe="1d",
                confidence_score=0.7 + i * 0.02,
            )
            for i, sym in enumerate(["ETHUSDT", "SOLUSDT", "ADAUSDT", "DOTUSDT"])
        ]
        mock_risk = MagicMock()
        mock_risk.run.return_value = alt_entries

        patches, mocks = _build_patches(
            snapshots=[btc_snap] + alt_snaps,
            entries=alt_entries,
        )
        patches[f"{_PATCH_BASE}.QuantAgent"] = MagicMock(return_value=mock_quant)
        patches[f"{_PATCH_BASE}.StrategyAgent"] = MagicMock(return_value=mock_strategy)
        patches[f"{_PATCH_BASE}.RiskAgent"] = MagicMock(return_value=mock_risk)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            # Run non-dry to hit DB/Discord path where caps matter
            run_pipeline(dry_run=False)
        finally:
            for cm in ctx_managers:
                cm.stop()

        # BTC was skipped → correlation guard caps alts to 2
        signal_logger = mocks["signal_logger"]
        assert signal_logger.log.call_count <= 2

        # Discord notifier received at most 2 entries
        notifier = mocks["notifier"]
        notifier.post.assert_called_once()
        posted_entries = notifier.post.call_args[0][0]
        assert len(posted_entries) <= 2


class TestSignalCap:
    """Signal cap limits daily output to max_daily_signals."""

    def test_caps_to_configured_max(self):
        """More approved signals than max_daily_signals should be capped."""
        entries_8 = [
            _make_entry(
                symbol=f"PAIR{i}USDT",
                confidence_score=0.65 + i * 0.02,
            )
            for i in range(8)
        ]

        patches, mocks = _build_patches(entries=entries_8)
        snaps = [
            PairSnapshot(
                symbol=f"PAIR{i}USDT", timeframe="1d", current_price=100.0,
                bar_timestamp=1_700_000_000_000, indicators=TAIndicators(),
                recent_closes=[100.0], recent_volumes=[500.0],
            )
            for i in range(8)
        ]
        mock_builder = MagicMock()
        mock_builder.build_universe.return_value = snaps
        patches[f"{_PATCH_BASE}.SnapshotBuilder"] = MagicMock(return_value=mock_builder)

        mock_quant = MagicMock()
        mock_quant.run.return_value = _make_quant_signal()
        patches[f"{_PATCH_BASE}.QuantAgent"] = MagicMock(return_value=mock_quant)

        buy_proposal = _make_proposal()
        mock_strategy = MagicMock()
        mock_strategy.run.return_value = buy_proposal
        patches[f"{_PATCH_BASE}.StrategyAgent"] = MagicMock(return_value=mock_strategy)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            with patch(f"{_PATCH_BASE}.load_risk_profile", return_value={"max_daily_signals": 5}):
                run_pipeline(dry_run=False)
        finally:
            for cm in ctx_managers:
                cm.stop()

        # Discord notifier should receive at most 5 entries
        notifier = mocks["notifier"]
        notifier.post.assert_called_once()
        posted_entries = notifier.post.call_args[0][0]
        assert len(posted_entries) <= 5
