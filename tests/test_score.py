"""Tests for score/model.py — size_score(), score_clusters(), tier()."""
import pytest
import numpy as np
from score.model import size_score, score_clusters, tier, WEIGHTS


# ---------------------------------------------------------------------------
# size_score
# ---------------------------------------------------------------------------

def test_size_score_peak_at_4():
    assert size_score(4) == pytest.approx(1.0)


def test_size_score_peak_greater_than_extremes():
    assert size_score(4) > size_score(1)
    assert size_score(4) > size_score(20)


# ---------------------------------------------------------------------------
# score_clusters
# ---------------------------------------------------------------------------

def _dummy_row(**overrides):
    base = {k: 0.0 for k in WEIGHTS}
    base.update(overrides)
    return base


def test_score_clusters_empty_returns_empty_array():
    result = score_clusters([])
    assert isinstance(result, np.ndarray)
    assert len(result) == 0


def test_score_clusters_single_item_scores_by_absolute():
    """With 1 item, absolute features use raw value; percentile features default to 1.0."""
    # All-zeros row: absolute features → 0, percentile features → 1.0 (ones_like fallback)
    row = _dummy_row()
    result = score_clusters([row])
    assert len(result) == 1
    # percentile features (co_tenure=0.14, open_to=0.06) → weight 0.20 * 100 = 20.0
    from score.model import WEIGHTS, PERCENTILE_FEATURES
    expected = sum(w for k, w in WEIGHTS.items() if k in PERCENTILE_FEATURES) * 100
    assert result[0] == pytest.approx(expected)


def test_strong_cluster_reaches_high_tier():
    from score.model import score_clusters, tier
    rows = [
        {  # genuinely strong: all converging, stealth, tight window, tiny dest
            "size_score": 1.0,
            "shared_destination": 1.0,
            "destination_tiny": 1.0,
            "stealth_founder_ratio": 1.0,
            "window_tightness": 1.0,
            "co_tenure": 1.0,
            "open_to": 1.0,
        },
        {  # weak noise cluster
            "size_score": 0.3,
            "shared_destination": 0.0,
            "destination_tiny": 0.0,
            "stealth_founder_ratio": 0.0,
            "window_tightness": 0.3,
            "co_tenure": 0.0,
            "open_to": 0.0,
        },
    ]
    scores = score_clusters(rows)
    assert tier(float(scores[0])) == "High", f"Expected High, got {tier(float(scores[0]))} (score={scores[0]:.1f})"
    assert float(scores[0]) >= 75


# ---------------------------------------------------------------------------
# tier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    (75.0, "High"),
    (74.9, "Medium"),
    (50.0, "Medium"),
    (49.9, "Low"),
    (25.0, "Low"),
    (24.9, "Watch"),
])
def test_tier_boundaries(score, expected):
    assert tier(score) == expected
