"""The eval harness: what it measures, and what it refuses to flatter."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipelinemd.corpus import load_corpus
from pipelinemd.evaluate import (
    EvalReport,
    eval_report_to_dict,
    evaluate_case,
    format_report,
    run_eval,
)

REPORT = run_eval()


# -- shape ------------------------------------------------------------------


def test_every_corpus_case_is_scored() -> None:
    assert len(REPORT.results) == len(load_corpus())


def test_the_eval_is_deterministic() -> None:
    """`make eval` is a regression gate; a moving number would be useless."""
    assert eval_report_to_dict(run_eval()) == eval_report_to_dict(run_eval())


def test_class_scores_partition_the_corpus() -> None:
    scores = REPORT.class_scores()
    assert sum(score.cases for score in scores) == len(REPORT.results)
    assert sum(score.labelled for score in scores) == len(REPORT.labelled)


def test_labelled_and_gaps_are_disjoint_and_complete() -> None:
    assert len(REPORT.labelled) + len(REPORT.gaps) == len(REPORT.results)


# -- semantics --------------------------------------------------------------


def test_a_gap_is_never_scored_as_correct() -> None:
    """An unlabelled case cannot be 'right' - there is no right answer."""
    assert all(not result.rule_correct for result in REPORT.gaps)


def test_rule_accuracy_counts_only_labelled_cases() -> None:
    correct = sum(result.rule_correct for result in REPORT.results)
    assert REPORT.rule_accuracy == pytest.approx(correct / len(REPORT.labelled))


def test_a_miss_is_a_labelled_case_that_did_not_rank_first() -> None:
    for miss in REPORT.misses:
        assert miss.case.expected_rule is not None
        assert miss.rank != 1


def test_rank_is_one_based_and_absent_when_the_rule_never_fired() -> None:
    for result in REPORT.results:
        if result.rank is not None:
            assert result.rank >= 1
            assert result.case.expected_rule in result.fired_rules


# -- the honesty guarantees -------------------------------------------------


def test_an_all_authored_corpus_says_so_in_the_report() -> None:
    """The caveat is the point; a bare percentage would read as measured."""
    text = format_report(REPORT)
    assert "authored, not observed" in text
    assert "regression" in text


def test_provenance_is_reported_not_collapsed() -> None:
    payload = eval_report_to_dict(REPORT)
    assert payload["provenance"] == {"authored": len(REPORT.results)}


def test_report_names_its_misses_and_gaps() -> None:
    text = format_report(REPORT)
    for miss in REPORT.misses:
        assert miss.case.id in text
    for gap in REPORT.gaps:
        assert gap.case.id in text


def test_a_gap_that_fires_a_rule_is_flagged_as_a_false_positive() -> None:
    payload = eval_report_to_dict(REPORT)
    for gap in payload["gaps"]:  # type: ignore[union-attr]
        assert gap["false_positive"] == (gap["top_rule"] is not None)


# -- per-case ---------------------------------------------------------------


def test_evaluate_case_reads_the_exit_code_and_finds_the_marker() -> None:
    case = next(c for c in load_corpus() if c.id == "runner-oom-killed")
    result = evaluate_case(case)
    assert result.exit_code_read == case.exit_code
    assert result.evidence_hit
    assert result.top_rule == "runner.oom-killed"


def test_json_payload_is_serialisable_and_versioned() -> None:
    payload = json.loads(json.dumps(eval_report_to_dict(REPORT)))
    assert payload["schema_version"] == 1
    assert 0.0 <= payload["overall"]["rule_at_1"] <= 1.0


# -- a corpus of one --------------------------------------------------------


def _mini(
    tmp_path: Path,
    *,
    expected_rule: str | None,
    marker: str,
    pad: int = 0,
    case_id: str = "one",
) -> Path:
    (tmp_path / "traces").mkdir()
    body = "".join(f"npm http fetch GET 200 registry/pkg-{i} 12ms\n" for i in range(pad))
    (tmp_path / "traces" / "one.log").write_text(
        "Running with gitlab-runner 16.11.0 (abc)\n"
        "$ npm ci\n" + body + "npm ERR! code ERESOLVE\n"
        "ERROR: Job failed: exit code 1\n",
        encoding="utf-8",
    )
    (tmp_path / "corpus.jsonl").write_text(
        json.dumps(
            {
                "id": case_id,
                "failure_class": "test",
                "expected_rule": expected_rule,
                "exit_code": 1,
                "evidence_marker": marker,
                "trace": "traces/one.log",
                "provenance": "observed",
                "notes": "synthetic",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_a_hit_scores_one(tmp_path: Path) -> None:
    report = run_eval(
        _mini(tmp_path, expected_rule="npm.eresolve", marker="npm ERR! code ERESOLVE")
    )
    assert report.rule_accuracy == 1.0
    assert report.evidence_hit_rate == 1.0
    assert report.exit_code_accuracy == 1.0


def test_a_wrong_label_scores_zero(tmp_path: Path) -> None:
    report = run_eval(
        _mini(tmp_path, expected_rule="runner.no-space", marker="npm ERR! code ERESOLVE")
    )
    assert report.rule_accuracy == 0.0
    assert report.misses[0].top_rule == "npm.eresolve"


def test_a_marker_the_distiller_drops_scores_zero(tmp_path: Path) -> None:
    """Evidence hit rate is a real measurement, not a restatement of the label.

    The marker sits at the top of a long trace, far outside the window the
    distiller keeps, so a harness that merely echoed the label would score 1.0
    here.
    """
    corpus = _mini(
        tmp_path,
        expected_rule="npm.eresolve",
        marker="Running with gitlab-runner",
        pad=800,
    )
    report = run_eval(corpus)
    assert report.results[0].rule_correct, "the rule must still be found"
    assert report.evidence_hit_rate == 0.0, "but the dropped marker must score zero"


def test_observed_provenance_drops_the_authored_caveat(tmp_path: Path) -> None:
    text = format_report(run_eval(_mini(tmp_path, expected_rule="npm.eresolve", marker="ERESOLVE")))
    assert "authored, not observed" not in text


def test_empty_report_does_not_divide_by_zero() -> None:
    empty = EvalReport(results=())
    assert empty.rule_accuracy == 0.0
    assert empty.evidence_hit_rate == 0.0
    assert empty.exit_code_accuracy == 0.0


# -- false positives on known gaps ------------------------------------------
#
# A gap case that starts firing a rule moves none of the three rates, because
# gaps are excluded from all of them. It is also the most alarming thing the
# eval can report, so it is counted and gateable separately.


def test_the_real_corpus_has_no_false_positives() -> None:
    """The anchor: every gap is correctly silent today. This is what regresses."""
    assert REPORT.gap_false_positives == ()


def test_a_gap_that_fires_a_rule_is_counted(tmp_path: Path) -> None:
    report = run_eval(_mini(tmp_path, expected_rule=None, marker="ERESOLVE"))
    assert len(report.gap_false_positives) == 1
    assert report.gap_false_positives[0].top_rule == "npm.eresolve"


def test_a_false_positive_moves_none_of_the_three_rates(tmp_path: Path) -> None:
    """Exactly why it needs its own count: the headline numbers stay perfect."""
    report = run_eval(_mini(tmp_path, expected_rule=None, marker="ERESOLVE"))
    assert report.rule_accuracy == 0.0  # no labelled cases at all
    assert report.evidence_hit_rate == 1.0
    assert report.exit_code_accuracy == 1.0
    assert report.gap_false_positives


def test_a_false_positive_is_stated_next_to_the_headline_numbers(tmp_path: Path) -> None:
    text = format_report(run_eval(_mini(tmp_path, expected_rule=None, marker="ERESOLVE")))
    assert "1 false positive(s) on known gaps" in text
    assert "FALSE POSITIVE: npm.eresolve" in text


def test_the_json_payload_aggregates_false_positives(tmp_path: Path) -> None:
    """A per-case flag cannot be gated on; a count in `overall` can."""
    payload = eval_report_to_dict(run_eval(_mini(tmp_path, expected_rule=None, marker="ERESOLVE")))
    overall = payload["overall"]
    assert isinstance(overall, dict)
    assert overall["gap_false_positives"] == 1
    assert eval_report_to_dict(REPORT)["overall"]["gap_false_positives"] == 0  # type: ignore[index]


# -- report layout ----------------------------------------------------------


def _corpus_with_ids(tmp_path: Path, *case_ids: str) -> Path:
    """One trace, several labels, so the report has rows of differing id length."""
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / "one.log").write_text(
        "Running with gitlab-runner 16.11.0 (abc)\n"
        "$ npm ci\n"
        "npm ERR! code ERESOLVE\n"
        "ERROR: Job failed: exit code 1\n",
        encoding="utf-8",
    )
    (tmp_path / "corpus.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "id": case_id,
                    "failure_class": "test",
                    "expected_rule": "runner.no-space",
                    "exit_code": 1,
                    "evidence_marker": "ERESOLVE",
                    "trace": "traces/one.log",
                    "provenance": "observed",
                    "notes": "synthetic",
                }
            )
            + "\n"
            for case_id in case_ids
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_a_long_id_widens_the_column_rather_than_skewing_its_neighbours(
    tmp_path: Path,
) -> None:
    """The width was hardcoded to 34; an id past that used to push its row out.

    Two rows are the point: with one, nothing is visibly misaligned. The
    corpus's longest id is 29 today, so the real report cannot catch this.
    """
    short, long = "short-id", "a-deliberately-very-long-corpus-case-identifier"
    assert len(long) > 34 > len(short)
    text = format_report(run_eval(_corpus_with_ids(tmp_path, short, long)))
    starts = {
        2 + len(match.group(1)) + len(match.group(2))
        for match in (re.match(r"  (\S+)( +)expected", line) for line in text.splitlines())
        if match
    }
    assert len(starts) == 1, f"the two rows start their second column at {sorted(starts)}"


def test_misses_and_gaps_share_one_column_width(tmp_path: Path) -> None:
    """The two blocks are read together, so they are measured together."""
    text = format_report(REPORT)
    listed = {r.case.id for r in REPORT.misses + REPORT.gaps}
    starts = set()
    for line in text.splitlines():
        match = re.match(r"  (\S+)( +)\S", line)
        if match and match.group(1) in listed:
            starts.add(2 + len(match.group(1)) + len(match.group(2)))
    assert len(starts) == 1, f"ragged id column: {sorted(starts)}"
