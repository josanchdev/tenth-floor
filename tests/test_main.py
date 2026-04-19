"""Tests for the daily pipeline orchestrator (Phase 1.5: AI-first)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from tenth_floor.data.models import (
    MacroRegime,
    MacroSignal,
    PairSnapshot,
    PlaybookEntry,
    PlaybookVerdict,
    ReviewedSignal,
    ReviewVerdict,
    SentimentSnapshot,
    SetupAction,
    SignalDirection,
    TAIndicators,
    TradeProposal,
)
from tenth_floor.main import FunnelTracker, _pre_screen, run_pipeline

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ohlcv_df(rows: int = 50) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": [1_700_000_000_000 + i * 14_400_000 for i in range(rows)],
        "open": [100.0] * rows,
        "high": [105.0] * rows,
        "low": [95.0] * rows,
        "close": [102.0] * rows,
        "volume": [1000.0] * rows,
    })


def _make_snapshot(symbol: str = "BTCUSDT", **kw) -> PairSnapshot:
    defaults = {
        "symbol": symbol,
        "timeframe": "1d",
        "current_price": 62_000.0,
        "bar_timestamp": 1_700_000_000_000,
        "indicators": TAIndicators(),
        "sentiment": None,
        "recent_closes": [62_000.0] * 15,
        "recent_volumes": [1000.0] * 15,
    }
    defaults.update(kw)
    return PairSnapshot(**defaults)


def _make_macro() -> MacroSignal:
    return MacroSignal(
        regime=MacroRegime.RISK_ON,
        regime_reasoning="Low VIX, greed sentiment.",
        asset_class_impacts=[],
        alerts=[],
        vix_level=15.0,
        fear_greed_value=65,
        dxy_trend="stable",
    )


def _make_proposal(symbol: str = "BTCUSDT", **kw) -> TradeProposal:
    defaults = {
        "symbol": symbol,
        "timeframe": "1d",
        "action": SetupAction.BUY,
        "direction": SignalDirection.LONG,
        "entry_price": 62_000.0,
        "stop_loss": 61_000.0,
        "take_profit": 64_000.0,
        "rationale": "Strong setup.",
        "confluence_factors": ["EMA alignment"],
        "risk_factors": [],
    }
    defaults.update(kw)
    return TradeProposal(**defaults)


def _make_reviewed(symbol: str = "BTCUSDT", **kw) -> ReviewedSignal:
    defaults = {
        "symbol": symbol,
        "verdict": ReviewVerdict.APPROVE,
        "conviction": "high",
        "confidence": 0.82,
        "reasoning": "Strong setup.",
        "risk_notes": "",
    }
    defaults.update(kw)
    return ReviewedSignal(**defaults)


def _make_entry(symbol: str = "BTCUSDT", **kw) -> PlaybookEntry:
    defaults = {
        "symbol": symbol,
        "timeframe": "1d",
        "report_date": "2026-03-31",
        "verdict": PlaybookVerdict.APPROVED,
        "verdict_reasoning": "Meets threshold",
        "direction": SignalDirection.LONG,
        "entry_price": 62_000.0,
        "stop_loss": 61_000.0,
        "take_profit": 64_000.0,
        "reward_risk_ratio": 2.5,
        "confidence_score": 0.82,
        "conviction": "high",
        "suggested_risk_pct": 0.02,
        "rationale": "Strong setup.",
        "risk_notes": "",
        "rank": 1,
    }
    defaults.update(kw)
    return PlaybookEntry(**defaults)


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

_PATCH_BASE = "tenth_floor.main"


def _build_patches(
    *,
    snapshots=None,
    macro=None,
    proposals=None,
    reviewed=None,
):
    """Return a dict of mock targets and their return values."""
    if snapshots is None:
        snapshots = [_make_snapshot()]
    if macro is None:
        macro = _make_macro()
    if proposals is None:
        proposals = [_make_proposal()]
    if reviewed is None:
        reviewed = [_make_reviewed()]

    mock_fetcher = MagicMock()
    mock_fetcher.fetch_universe.return_value = {"BTCUSDT": {"1d": _make_ohlcv_df()}}
    # Re-snap step asks for a live price for each survivor — return the
    # snapshot's current_price so validation passes unchanged.
    mock_fetcher.fetch_last_price.return_value = 62_000.0

    mock_yf = MagicMock()
    mock_yf.fetch_last_price.return_value = 62_000.0

    mock_sentiment = MagicMock()
    mock_sentiment.fetch_snapshot.return_value = SentimentSnapshot(
        fear_greed_value=65,
        fear_greed_label="Greed",
    )

    mock_builder = MagicMock()
    mock_builder.build_universe.return_value = snapshots

    mock_macro_analyst = MagicMock()
    mock_macro_analyst.run.return_value = macro

    mock_trade_analyst = MagicMock()
    if len(proposals) == 1:
        mock_trade_analyst.run.return_value = proposals[0]
    else:
        mock_trade_analyst.run.side_effect = proposals

    mock_risk_reviewer = MagicMock()
    mock_risk_reviewer.run.return_value = reviewed

    mock_signal_logger = MagicMock()
    mock_signal_logger.log.return_value = "BTCUSDT_2026-03-31_abc12345"
    mock_signal_logger.open_signal_count.return_value = 3
    mock_signal_logger.get_active_signals.return_value = []
    mock_signal_logger.__enter__ = MagicMock(return_value=mock_signal_logger)
    mock_signal_logger.__exit__ = MagicMock(return_value=False)

    patches = {
        f"{_PATCH_BASE}.MarketDataFetcher": MagicMock(return_value=mock_fetcher),
        f"{_PATCH_BASE}.YFinanceDataFetcher": MagicMock(return_value=mock_yf),
        f"{_PATCH_BASE}.SentimentFetcher": MagicMock(return_value=mock_sentiment),
        f"{_PATCH_BASE}.SnapshotBuilder": MagicMock(return_value=mock_builder),
        f"{_PATCH_BASE}.MacroAnalyst": MagicMock(return_value=mock_macro_analyst),
        f"{_PATCH_BASE}.TradeAnalyst": MagicMock(return_value=mock_trade_analyst),
        f"{_PATCH_BASE}.RiskReviewer": MagicMock(return_value=mock_risk_reviewer),
        f"{_PATCH_BASE}.SignalLogger": MagicMock(return_value=mock_signal_logger),
        f"{_PATCH_BASE}._fetch_macro_indicators": MagicMock(return_value=(None, None)),
    }

    mocks = {
        "fetcher": mock_fetcher,
        "sentiment_fetcher": mock_sentiment,
        "builder": mock_builder,
        "macro_analyst": mock_macro_analyst,
        "trade_analyst": mock_trade_analyst,
        "risk_reviewer": mock_risk_reviewer,
        "signal_logger": mock_signal_logger,
    }

    return patches, mocks


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
    """Test the end-to-end Phase 1.5 pipeline wiring."""

    def test_full_pipeline_returns_entries(self):
        entries, _ = _run_with_patches()
        assert len(entries) == 1
        assert entries[0].verdict == PlaybookVerdict.APPROVED

    def test_calls_agents_in_order(self):
        _, mocks = _run_with_patches()

        mocks["macro_analyst"].run.assert_called_once()
        mocks["trade_analyst"].run.assert_called_once()
        mocks["risk_reviewer"].run.assert_called_once()

    def test_logs_approved_signals_to_db(self):
        _, mocks = _run_with_patches()

        mocks["signal_logger"].log.assert_called_once()
        mocks["signal_logger"].open_signal_count.assert_called_once()

    def test_dry_run_skips_db(self):
        entries, mocks = _run_with_patches(dry_run=True)

        assert len(entries) == 1
        mocks["signal_logger"].log.assert_not_called()

    def test_empty_snapshots_returns_empty(self):
        entries, _ = _run_with_patches(snapshots=[])
        assert entries == []

    def test_trade_analyst_skip_not_sent_to_reviewer(self):
        """TradeAnalyst SKIP proposals should not reach RiskReviewer."""
        skip_proposal = _make_proposal(action=SetupAction.SKIP, direction=SignalDirection.NEUTRAL)
        _, mocks = _run_with_patches(proposals=[skip_proposal], reviewed=[])

        # No BUY proposals → RiskReviewer is never called
        mocks["risk_reviewer"].run.assert_not_called()

    def test_trade_analyst_error_skips_asset(self):
        """If TradeAnalyst raises for one asset, pipeline continues."""
        snap1 = _make_snapshot("BTCUSDT")
        snap2 = _make_snapshot("ETHUSDT")

        patches, mocks = _build_patches(snapshots=[snap1, snap2])

        mock_ta = MagicMock()
        mock_ta.run.side_effect = [_make_proposal(), RuntimeError("LLM timeout")]
        patches[f"{_PATCH_BASE}.TradeAnalyst"] = MagicMock(return_value=mock_ta)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            run_pipeline(dry_run=True)
        finally:
            for cm in ctx_managers:
                cm.stop()

        assert mock_ta.run.call_count == 2

    def test_macro_analyst_failure_aborts(self):
        """If MacroAnalyst fails, pipeline aborts."""
        patches, mocks = _build_patches()
        mock_macro = MagicMock()
        mock_macro.run.side_effect = RuntimeError("LLM down")
        patches[f"{_PATCH_BASE}.MacroAnalyst"] = MagicMock(return_value=mock_macro)

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            entries = run_pipeline(dry_run=True)
        finally:
            for cm in ctx_managers:
                cm.stop()

        assert entries == []

    def test_rejected_entries_not_logged(self):
        """Rejected entries from RiskReviewer should not be logged to DB."""
        rejected = _make_reviewed(verdict=ReviewVerdict.REJECT)
        entries, mocks = _run_with_patches(reviewed=[rejected])

        assert len(entries) == 0
        mocks["signal_logger"].log.assert_not_called()


class TestPreScreen:
    """Test data-quality pre-screen."""

    def test_passes_normal_snapshot(self):
        funnel = FunnelTracker()
        snap = _make_snapshot()
        result = _pre_screen([snap], funnel)
        assert len(result) == 1
        assert funnel.pre_screen_passed == 1

    def test_kills_insufficient_data(self):
        funnel = FunnelTracker()
        snap = _make_snapshot(recent_closes=[62000.0] * 5)  # only 5 closes
        result = _pre_screen([snap], funnel)
        assert len(result) == 0
        assert funnel.pre_screen_killed == 1

    def test_kills_zero_volume(self):
        funnel = FunnelTracker()
        snap = _make_snapshot(recent_volumes=[0.0] * 15)
        result = _pre_screen([snap], funnel)
        assert len(result) == 0
        assert funnel.pre_screen_killed == 1


class TestSignalCap:
    """Signal cap limits daily output to max_daily_signals."""

    def test_caps_to_configured_max(self):
        """More approved signals than max → top-N PUBLISHED, rest SESSION."""
        proposals = [_make_proposal(symbol=f"P{i}USDT") for i in range(5)]
        reviewed = [_make_reviewed(symbol=f"P{i}USDT") for i in range(5)]
        snaps = [_make_snapshot(symbol=f"P{i}USDT") for i in range(5)]

        patches, mocks = _build_patches(
            snapshots=snaps, proposals=proposals, reviewed=reviewed,
        )

        ctx_managers = [patch(target, val) for target, val in patches.items()]
        for cm in ctx_managers:
            cm.start()
        try:
            with patch(f"{_PATCH_BASE}.load_risk_profile", return_value={"max_daily_signals": 2}):
                run_pipeline(dry_run=False)
        finally:
            for cm in ctx_managers:
                cm.stop()

        # All 5 are persisted; the split is 2 PUBLISHED + 3 SESSION.
        log_mock = mocks["signal_logger"].log
        assert log_mock.call_count == 5
        tiers = [call.kwargs.get("tier") for call in log_mock.call_args_list]
        assert tiers.count("PUBLISHED") == 2
        assert tiers.count("SESSION") == 3


class TestCLI:
    """Test CLI argument parsing."""

    def test_parse_args_defaults(self):
        from tenth_floor.main import _parse_args
        args = _parse_args([])
        assert args.pairs is None
        assert args.dry_run is False
        assert args.log_level == "INFO"

    def test_parse_args_with_symbols(self):
        from tenth_floor.main import _parse_args
        args = _parse_args(["BTCUSDT", "AAPL"])
        assert args.pairs == ["BTCUSDT", "AAPL"]

    def test_parse_args_dry_run(self):
        from tenth_floor.main import _parse_args
        args = _parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_parse_args_profile(self):
        from tenth_floor.main import _parse_args
        args = _parse_args(["--profile", "validation"])
        assert args.profile == "validation"

    def test_parse_args_asset_class(self):
        from tenth_floor.main import _parse_args
        args = _parse_args(["--asset-class", "crypto"])
        assert args.asset_class == "crypto"
