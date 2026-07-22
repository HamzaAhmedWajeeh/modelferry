"""CLI wiring: version, help, and that the offline wrappers reach offline.main."""

import pytest
from typer.testing import CliRunner

from modelferry import __version__
from modelferry.cli import app

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_all_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("pack", "verify", "unpack", "inspect", "verify-signature"):
        assert command in result.stdout


def test_pack_bad_chunk_size_is_usage_error(tmp_path):
    # parse_chunk_size runs before any hub access, so this needs no network.
    result = runner.invoke(
        app, ["pack", "acme/demo", "--dest", str(tmp_path), "--chunk-size", "bogus"]
    )
    assert result.exit_code == 2


@pytest.mark.parametrize("command", ["verify", "inspect"])
def test_offline_wrapper_reports_missing_bundle(tmp_path, command):
    # The wrapper delegates to offline.main, which exits 2 on a non-bundle dir.
    result = runner.invoke(app, [command, str(tmp_path / "nope")])
    assert result.exit_code == 2
