"""Pure Python OCR evaluation metrics — no external dependencies.

Provides:
- levenshtein_distance / character_error_rate
- Digit multiset precision/recall/F1
- Lottery number token (01-39) extraction and multiset metrics
- Variant ranking by ground truth
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Levenshtein (two-row DP, O(n) memory) ────────────────────────────────────


def levenshtein_distance(expected: str, actual: str) -> int:
    """Compute edit distance using two-row DP."""
    if len(expected) < len(actual):
        expected, actual = actual, expected
    prev = list(range(len(actual) + 1))
    curr = [0] * (len(actual) + 1)
    for i, ec in enumerate(expected, 1):
        curr[0] = i
        for j, ac in enumerate(actual, 1):
            cost = 0 if ec == ac else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[len(actual)]


def character_error_rate(expected: str, actual: str) -> float:
    """CER = edit_distance / len(expected). Edge cases handled."""
    if not expected and not actual:
        return 0.0
    if not expected:
        return 1.0
    if not actual:
        return 1.0
    return min(1.0, levenshtein_distance(expected, actual) / len(expected))


@dataclass
class CharacterMetrics:
    edit_distance: int = 0
    expected_chars: int = 0
    actual_chars: int = 0
    cer: float = 0.0
    exact_match: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_distance": self.edit_distance,
            "expected_chars": self.expected_chars,
            "actual_chars": self.actual_chars,
            "cer": round(self.cer, 4),
            "exact_match": self.exact_match,
        }


def evaluate_characters(expected: str, actual: str) -> CharacterMetrics:
    dist = levenshtein_distance(expected, actual)
    return CharacterMetrics(
        edit_distance=dist,
        expected_chars=len(expected),
        actual_chars=len(actual),
        cer=character_error_rate(expected, actual),
        exact_match=expected == actual,
    )


# ── Digit multiset metrics ───────────────────────────────────────────────────


def _extract_digits(text: str) -> list[str]:
    return [ch for ch in text if ch.isdigit()]


@dataclass
class DigitMetrics:
    expected_count: int = 0
    actual_count: int = 0
    matched_count: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_count": self.expected_count,
            "actual_count": self.actual_count,
            "matched_count": self.matched_count,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "note": "digit metrics are multiset character metrics, NOT number token accuracy",
        }


def evaluate_digits(expected: str, actual: str) -> DigitMetrics:
    exp_digits = _extract_digits(expected)
    act_digits = _extract_digits(actual)

    if not exp_digits and not act_digits:
        return DigitMetrics(precision=1.0, recall=1.0, f1=1.0)
    if not exp_digits:
        return DigitMetrics(actual_count=len(act_digits))
    if not act_digits:
        return DigitMetrics(expected_count=len(exp_digits))

    # Multiset matching
    from collections import Counter
    exp_c = Counter(exp_digits)
    act_c = Counter(act_digits)
    matched = sum((exp_c & act_c).values())

    precision = matched / len(act_digits) if act_digits else 0.0
    recall = matched / len(exp_digits) if exp_digits else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return DigitMetrics(
        expected_count=len(exp_digits),
        actual_count=len(act_digits),
        matched_count=matched,
        precision=precision,
        recall=recall,
        f1=f1,
    )


# ── Lottery number token extraction ──────────────────────────────────────────

_LOTTERY_NUMBER_RE = re.compile(r"(?<!\d)\d{1,2}(?!\d)")
_LOTTERY_RANGE = set(range(1, 40))


def _canonical_two_digit(token: str) -> str:
    """Convert 1→01, 5→05, 09→09, 39→39."""
    n = int(token)
    return f"{n:02d}"


def _extract_all_numeric_tokens(text: str) -> list[str]:
    return _LOTTERY_NUMBER_RE.findall(text)


def _extract_lottery_tokens(text: str) -> list[str]:
    tokens = _extract_all_numeric_tokens(text)
    result = []
    for t in tokens:
        try:
            n = int(t)
            if n in _LOTTERY_RANGE:
                result.append(_canonical_two_digit(t))
        except ValueError:
            pass
    return result


@dataclass
class TokenMetrics:
    expected_count: int = 0
    actual_count: int = 0
    matched_count: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    missing_tokens: list[str] = field(default_factory=list)
    extra_tokens: list[str] = field(default_factory=list)
    all_numeric_actual: list[str] = field(default_factory=list)
    lottery_actual: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_count": self.expected_count,
            "actual_count": self.actual_count,
            "matched_count": self.matched_count,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "missing_tokens": self.missing_tokens,
            "extra_tokens": self.extra_tokens,
            "all_numeric_actual": self.all_numeric_actual,
            "lottery_actual": self.lottery_actual,
            "note": "lottery token metrics use multiset (01-39 only), NOT full bet accuracy",
        }


def evaluate_lottery_tokens(expected: str, actual: str) -> TokenMetrics:
    exp_tokens = _extract_lottery_tokens(expected)
    act_tokens = _extract_lottery_tokens(actual)
    all_numeric = _extract_all_numeric_tokens(actual)

    if not exp_tokens and not act_tokens:
        return TokenMetrics(
            all_numeric_actual=all_numeric,
            lottery_actual=act_tokens,
            precision=1.0, recall=1.0, f1=1.0,
        )
    if not exp_tokens:
        return TokenMetrics(
            actual_count=len(act_tokens),
            extra_tokens=list(act_tokens),
            all_numeric_actual=all_numeric,
            lottery_actual=act_tokens,
        )
    if not act_tokens:
        return TokenMetrics(
            expected_count=len(exp_tokens),
            missing_tokens=list(exp_tokens),
            all_numeric_actual=all_numeric,
            lottery_actual=[],
        )

    from collections import Counter
    exp_c = Counter(exp_tokens)
    act_c = Counter(act_tokens)
    matched = sum((exp_c & act_c).values())

    precision = matched / len(act_tokens) if act_tokens else 0.0
    recall = matched / len(exp_tokens) if exp_tokens else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    missing = list((exp_c - act_c).elements())
    extra = list((act_c - exp_c).elements())

    return TokenMetrics(
        expected_count=len(exp_tokens),
        actual_count=len(act_tokens),
        matched_count=matched,
        precision=precision,
        recall=recall,
        f1=f1,
        missing_tokens=missing,
        extra_tokens=extra,
        all_numeric_actual=all_numeric,
        lottery_actual=act_tokens,
    )


# ── Line-level production metrics ────────────────────────────────────────────

_MULTIPLIER_RE = re.compile(
    r"(?i)(?:[x×*]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*支|\d+(?:\.\d+)?\s*元)"
)


def _normalize_line_for_exact(text: str) -> str:
    return re.sub(r"\s+", "", text.strip()).replace("×", "x").replace("X", "x")


def _extract_multiplier_tokens(text: str) -> list[str]:
    tokens = []
    for token in _MULTIPLIER_RE.findall(text):
        normalized = re.sub(r"\s+", "", token).replace("×", "x").replace("X", "x")
        tokens.append(normalized.lower())
    return tokens


def _remove_multiplier_tokens(text: str) -> str:
    return _MULTIPLIER_RE.sub(" ", text)


@dataclass
class LineAccuracyMetrics:
    line_count: int = 0
    line_exact_count: int = 0
    number_exact_count: int = 0
    number_multiplier_exact_count: int = 0
    human_correction_needed_count: int = 0
    line_exact_accuracy: float = 0.0
    number_exact_accuracy: float = 0.0
    number_multiplier_exact_accuracy: float = 0.0
    human_correction_line_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_count": self.line_count,
            "line_exact_count": self.line_exact_count,
            "number_exact_count": self.number_exact_count,
            "number_multiplier_exact_count": self.number_multiplier_exact_count,
            "human_correction_needed_count": self.human_correction_needed_count,
            "line_exact_accuracy": round(self.line_exact_accuracy, 4),
            "number_exact_accuracy": round(self.number_exact_accuracy, 4),
            "number_multiplier_exact_accuracy": round(self.number_multiplier_exact_accuracy, 4),
            "human_correction_line_ratio": round(self.human_correction_line_ratio, 4),
        }


def evaluate_line_accuracy(
    expected_lines: list[str],
    actual_lines: list[str],
    *,
    uncertain_lines: set[int] | None = None,
) -> LineAccuracyMetrics:
    """Compute line, number, number+multiplier, and correction-needed rates."""
    uncertain_lines = uncertain_lines or set()
    count = max(len(expected_lines), len(actual_lines))
    metrics = LineAccuracyMetrics(line_count=count)
    if count == 0:
        metrics.line_exact_accuracy = 1.0
        metrics.number_exact_accuracy = 1.0
        metrics.number_multiplier_exact_accuracy = 1.0
        metrics.human_correction_line_ratio = 0.0
        return metrics

    for index in range(count):
        expected = expected_lines[index] if index < len(expected_lines) else ""
        actual = actual_lines[index] if index < len(actual_lines) else ""
        line_exact = _normalize_line_for_exact(expected) == _normalize_line_for_exact(actual)
        expected_numbers = _extract_lottery_tokens(_remove_multiplier_tokens(expected))
        actual_numbers = _extract_lottery_tokens(_remove_multiplier_tokens(actual))
        expected_multipliers = _extract_multiplier_tokens(expected)
        actual_multipliers = _extract_multiplier_tokens(actual)

        number_exact = expected_numbers == actual_numbers
        number_multiplier_exact = number_exact and expected_multipliers == actual_multipliers

        if line_exact:
            metrics.line_exact_count += 1
        if number_exact:
            metrics.number_exact_count += 1
        if number_multiplier_exact:
            metrics.number_multiplier_exact_count += 1
        if not line_exact or index in uncertain_lines:
            metrics.human_correction_needed_count += 1

    metrics.line_exact_accuracy = metrics.line_exact_count / count
    metrics.number_exact_accuracy = metrics.number_exact_count / count
    metrics.number_multiplier_exact_accuracy = metrics.number_multiplier_exact_count / count
    metrics.human_correction_line_ratio = metrics.human_correction_needed_count / count
    return metrics


# ── Variant evaluation ───────────────────────────────────────────────────────


@dataclass
class VariantEval:
    variant_id: str = ""
    cer: float = 1.0
    digit_metrics: DigitMetrics = field(default_factory=DigitMetrics)
    lottery_token_metrics: TokenMetrics = field(default_factory=TokenMetrics)
    exact_match: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cer": self.cer,
            "digit_metrics": self.digit_metrics.to_dict(),
            "lottery_token_metrics": self.lottery_token_metrics.to_dict(),
            "exact_match": self.exact_match,
        }


def evaluate_variant(expected: str, actual: str) -> VariantEval:
    return VariantEval(
        cer=character_error_rate(expected, actual),
        digit_metrics=evaluate_digits(expected, actual),
        lottery_token_metrics=evaluate_lottery_tokens(expected, actual),
        exact_match=expected == actual,
    )


def rank_by_ground_truth(variants: list[dict], ground_truth: str) -> tuple[str, list[dict]]:
    """Rank variants by: lottery F1 desc, CER asc, digit F1 desc, latency asc, index.

    Returns (best_variant_id, ranked_list).
    """
    scored = []
    for i, v in enumerate(variants):
        if v.get("status") != "completed":
            scored.append((v, -1.0, 1.0, 0.0, 1e9, i))
            continue
        raw = v.get("raw_text", "")
        eval_result = evaluate_variant(ground_truth, raw)
        v["_evaluation"] = eval_result.to_dict()
        scored.append((
            v,
            eval_result.lottery_token_metrics.f1,
            eval_result.cer,
            eval_result.digit_metrics.f1,
            v.get("elapsed_ms", 0),
            i,
        ))

    scored.sort(key=lambda x: (-x[1], x[2], -x[3], x[4], x[5]))
    best_id = f"{scored[0][0].get('rotation',0)}/{scored[0][0].get('preprocess_profile','')}/{scored[0][0].get('detection_profile','')}"

    # Clean up internal _evaluation
    ranked = []
    for v, *_ in scored:
        ranked.append(v)

    return best_id, ranked


# ── Aggregate metrics ────────────────────────────────────────────────────────


@dataclass
class AggregateMetrics:
    sample_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    mean_cer: float = 0.0
    median_cer: float = 0.0
    micro_digit_precision: float = 0.0
    micro_digit_recall: float = 0.0
    micro_digit_f1: float = 0.0
    micro_token_precision: float = 0.0
    micro_token_recall: float = 0.0
    micro_token_f1: float = 0.0
    macro_token_f1: float = 0.0
    exact_match_count: int = 0
    total_time_ms: float = 0.0
    mean_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "mean_cer": round(self.mean_cer, 4),
            "median_cer": round(self.median_cer, 4),
            "micro_digit_precision": round(self.micro_digit_precision, 4),
            "micro_digit_recall": round(self.micro_digit_recall, 4),
            "micro_digit_f1": round(self.micro_digit_f1, 4),
            "micro_token_precision": round(self.micro_token_precision, 4),
            "micro_token_recall": round(self.micro_token_recall, 4),
            "micro_token_f1": round(self.micro_token_f1, 4),
            "macro_token_f1": round(self.macro_token_f1, 4),
            "exact_match_count": self.exact_match_count,
            "total_time_ms": round(self.total_time_ms, 1),
            "mean_latency_ms": round(self.mean_latency_ms, 1),
        }


def compute_aggregate(sample_results: list[dict]) -> AggregateMetrics:
    """Compute aggregate metrics across multiple samples."""
    ag = AggregateMetrics(sample_count=len(sample_results))

    cer_list = []
    total_dig_matched = 0
    total_dig_exp = 0
    total_dig_act = 0
    total_tok_matched = 0
    total_tok_exp = 0
    total_tok_act = 0
    token_f1s = []
    total_time = 0.0
    latencies = []

    for sr in sample_results:
        if sr.get("status") == "completed":
            ag.completed_count += 1
        else:
            ag.failed_count += 1
            continue

        ev = sr.get("_evaluation", {})
        cer_list.append(ev.get("cer", 1.0))
        if ev.get("exact_match"):
            ag.exact_match_count += 1

        dm = ev.get("digit_metrics", {})
        total_dig_matched += dm.get("matched_count", 0)
        total_dig_exp += dm.get("expected_count", 0)
        total_dig_act += dm.get("actual_count", 0)

        tm = ev.get("lottery_token_metrics", {})
        total_tok_matched += tm.get("matched_count", 0)
        total_tok_exp += tm.get("expected_count", 0)
        total_tok_act += tm.get("actual_count", 0)
        token_f1s.append(tm.get("f1", 0.0))

        total_time += sr.get("elapsed_ms", 0)
        latencies.append(sr.get("elapsed_ms", 0))

    if cer_list:
        ag.mean_cer = sum(cer_list) / len(cer_list)
        ag.median_cer = sorted(cer_list)[len(cer_list) // 2]

    ag.micro_digit_precision = total_dig_matched / total_dig_act if total_dig_act else 0.0
    ag.micro_digit_recall = total_dig_matched / total_dig_exp if total_dig_exp else 0.0
    p, r = ag.micro_digit_precision, ag.micro_digit_recall
    ag.micro_digit_f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    ag.micro_token_precision = total_tok_matched / total_tok_act if total_tok_act else 0.0
    ag.micro_token_recall = total_tok_matched / total_tok_exp if total_tok_exp else 0.0
    p, r = ag.micro_token_precision, ag.micro_token_recall
    ag.micro_token_f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    ag.macro_token_f1 = sum(token_f1s) / len(token_f1s) if token_f1s else 0.0
    ag.total_time_ms = total_time
    ag.mean_latency_ms = total_time / ag.completed_count if ag.completed_count else 0.0

    return ag


# ── Ground truth file loading ────────────────────────────────────────────────


def load_ground_truth(path: str) -> str:
    """Load ground truth text file. Normalizes CRLF→LF, strips BOM, preserves intra-line whitespace."""
    import os
    if not path:
        return ""
    if "://" in path:
        raise ValueError("URL ground truth paths not supported")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Ground truth file not found: {path}")
    if os.path.islink(path):
        raise ValueError("Symlink ground truth paths not supported")

    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip trailing single newline only
    if text.endswith("\n") and not text.endswith("\n\n"):
        text = text[:-1]
    return text
