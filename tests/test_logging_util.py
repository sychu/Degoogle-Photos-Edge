"""Unit tests for console progress reporting (logging_util.ProgressBar)."""

from degoogle_photos.logging_util import ProgressBar


def test_prints_at_tick_multiples_and_final(capsys):
    bar = ProgressBar(tick=2)
    for i in range(1, 6):
        bar.update(i, 5)
    out = capsys.readouterr().out
    assert "2/5 (40.0%)" in out
    assert "4/5 (80.0%)" in out
    assert "5/5 (100.0%)" in out
    assert "1/5" not in out
    assert "3/5" not in out


def test_output_contains_percent_and_rate(capsys):
    bar = ProgressBar(tick=1)
    bar.update(1, 1)
    out = capsys.readouterr().out
    assert "%" in out
    assert "files/sec" in out


def test_stats_appended_when_provided(capsys):
    bar = ProgressBar(tick=1, stats=lambda: "copied=3 errors=0")
    bar.update(1, 1)
    out = capsys.readouterr().out
    assert "copied=3 errors=0" in out


def test_on_update_hook_is_called(capsys):
    seen = []
    bar = ProgressBar(tick=10, on_update=lambda c, t: seen.append((c, t)))
    bar.update(1, 5)
    capsys.readouterr()
    assert seen == [(1, 5)]


def test_finish_emits_newline(capsys):
    bar = ProgressBar(tick=1)
    bar.update(1, 1)
    bar.finish()
    out = capsys.readouterr().out
    assert out.endswith("\n")


def test_zero_total_does_not_divide_by_zero(capsys):
    bar = ProgressBar(tick=1)
    bar.update(0, 0)
    out = capsys.readouterr().out
    assert "0/0 (0.0%)" in out
    assert "files/sec" in out