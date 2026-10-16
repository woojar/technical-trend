"""The step output the weekly workflow uses to flag a degraded digest."""

from __future__ import annotations

from pathlib import Path

from trend.cli import _set_step_output


def test_step_output_written_for_actions(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    _set_step_output("llm_provider", "gemini")
    _set_step_output("other", "x")
    assert out.read_text().splitlines() == ["llm_provider=gemini", "other=x"]


def test_degraded_run_writes_empty_provider(tmp_path: Path, monkeypatch) -> None:
    """An empty value is what the workflow's failure step keys on."""
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    _set_step_output("llm_provider", "")
    assert out.read_text() == "llm_provider=\n"


def test_step_output_is_a_no_op_outside_actions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.chdir(tmp_path)
    _set_step_output("llm_provider", "gemini")
    assert list(tmp_path.iterdir()) == []
