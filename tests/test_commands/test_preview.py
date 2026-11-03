from unittest.mock import Mock

from click.testing import CliRunner
from PIL import Image

from praecentor.cli import main
from praecentor.commands import preview
from praecentor.controllers.errors import ControllerError


def preview_result(tmp_path):
    output = tmp_path / "preview.png"
    Image.new("RGB", (1920, 1080), "black").save(output)
    return {"output": str(output), "metadata_file": str(output.with_suffix(".json")),
            "draft_default": True, "fonts": [], "frames": {"lyrics": str(output)}}


def test_preview_cli_delegates_without_opening_a_viewer(tmp_path, monkeypatch):
    controller = Mock(return_value=preview_result(tmp_path))
    show = Mock()
    monkeypatch.setattr(preview, "preview_layout", controller)
    monkeypatch.setattr(Image.Image, "show", show)
    result = CliRunner().invoke(main, [
        "preview-layout", "--sample", "lyrics", "--output", str(tmp_path / "preview.png"), "--guides",
    ])
    assert result.exit_code == 0, result.output
    assert "visual approval is still pending" in result.output
    assert controller.call_args.kwargs["sample"] == "lyrics"
    assert controller.call_args.kwargs["guides"] is True
    show.assert_not_called()


def test_preview_cli_show_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, "preview_layout", lambda *args, **kwargs: preview_result(tmp_path))
    show = Mock()
    monkeypatch.setattr(Image.Image, "show", show)
    result = CliRunner().invoke(main, ["preview-layout", "--show"])
    assert result.exit_code == 0, result.output
    show.assert_called_once_with(title="Praecentor layout preview")


def test_preview_cli_reports_controller_errors(monkeypatch):
    monkeypatch.setattr(preview, "preview_layout", Mock(side_effect=ControllerError("Inter is missing")))
    result = CliRunner().invoke(main, ["preview-layout"])
    assert result.exit_code != 0
    assert "Inter is missing" in result.output
