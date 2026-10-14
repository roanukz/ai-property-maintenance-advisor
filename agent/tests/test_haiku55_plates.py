"""Decision 69: once the capabilities check records the plate photo as refused, plate runs refuse.

Driven through `advisor eval plates --live` on test_live_eval's fake clients; nothing is spent.
Each test names its mutation.
"""

from __future__ import annotations

import json

import pytest

from agent import cli, config
from agent.tests.test_live_eval import Fakes, _run, _spent_nothing, live_env  # noqa: F401  (a fixture)


def record(image_input: str, model: str = config.HAIKU) -> None:
    path = config.EVAL_DIR / config.CAPABILITIES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "image_input": image_input}), encoding="utf-8")


def test_plate_runs_refuse_once_the_photo_is_recorded_as_refused(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation haiku55_plate_eval_ignores_refusal: the plate runs start although the photo was refused."""
    record("refused")
    assert _run(["eval", "plates", "--live"], "proceed\n", monkeypatch) == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "refused the plate photo" in captured.err and "no other model" in captured.err
    assert "planned runs" not in captured.out
    _spent_nothing(live_env)

    # Accepted, or recorded for another model: the batch reaches its preflight as before.
    for image_input, model in (("accepted", config.HAIKU), ("refused", "an-older-model")):
        record(image_input, model)
        assert _run(["eval", "plates", "--live"], "no\n", monkeypatch) == cli.EXIT_REFUSED
        assert "planned runs: 2" in capsys.readouterr().out
    _spent_nothing(live_env)
