"""Phase 1 smoke tests: the CLI skeleton is wired and its commands are stubs."""

import pytest
from typer.testing import CliRunner

from modelferry import __version__
from modelferry.cli import app

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_all_four_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("pack", "verify", "unpack", "inspect"):
        assert command in result.stdout


@pytest.mark.parametrize(
    "argv",
    [
        ["pack", "some/repo", "--dest", "out"],
        ["verify", "bundle"],
        ["unpack", "bundle", "dest"],
        ["inspect", "bundle"],
    ],
)
def test_subcommands_are_stubs(argv):
    result = runner.invoke(app, argv)
    assert result.exit_code != 0
    assert isinstance(result.exception, NotImplementedError)
