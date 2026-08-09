from target_intel.literature.calibration import (
    TemperatureCalibrator,
    expected_calibration_error,
    fit_and_evaluate,
)


def test_calibrator_fits_and_produces_valid_probabilities():
    raw_scores = [0.9, 0.8, 0.7, 0.3, 0.2, 0.1]
    labels = [1, 1, 1, 0, 0, 0]
    calibrator = TemperatureCalibrator().fit(raw_scores, labels)
    for r in raw_scores:
        p = calibrator.calibrate(r)
        assert 0.0 <= p <= 1.0


def test_calibration_preserves_ranking():
    # Temperature scaling must not reorder scores - it only rescales confidence.
    raw_scores = [0.9, 0.5, 0.1]
    labels = [1, 1, 0]
    calibrator = TemperatureCalibrator().fit(raw_scores, labels)
    calibrated = [calibrator.calibrate(r) for r in raw_scores]
    assert calibrated[0] > calibrated[1] > calibrated[2]


def test_ece_is_low_for_near_perfectly_calibrated_predictions():
    # Bin avg_conf 0.9 vs bin accuracy 1.0 legitimately contributes ~0.09
    # (weighted by bin size) - "near-perfect", not exactly zero.
    probs = [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.1]
    labels = [1, 1, 1, 1, 1, 1, 1, 1, 1, 0]
    ece, _ = expected_calibration_error(probs, labels, n_bins=5)
    assert ece < 0.15


def test_ece_is_high_for_overconfident_wrong_predictions():
    probs = [0.95, 0.95, 0.95, 0.95]
    labels = [0, 0, 0, 0]  # confidently wrong every time
    ece, _ = expected_calibration_error(probs, labels, n_bins=5)
    assert ece > 0.8


def test_fit_and_evaluate_returns_consistent_result():
    raw_scores = [0.9, 0.8, 0.6, 0.4, 0.2, 0.1]
    labels = [1, 1, 1, 0, 0, 0]
    calibrator, result = fit_and_evaluate(raw_scores, labels, n_bins=5)
    assert result.n_examples == 6
    assert result.temperature == calibrator.temperature
    assert 0.0 <= result.ece <= 1.0
