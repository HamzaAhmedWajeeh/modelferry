"""Typer command-line surface for modelferry.

Phase 1 scaffold: the four subcommands from SPEC.md section 3 are wired up with
their argument surface but carry no implementation yet. Each raises
NotImplementedError until its phase lands (offline commands in Phase 2, pack in
Phase 3).
"""

from __future__ import annotations

from typing import Annotated

import typer

from . import __version__

app = typer.Typer(
    add_completion=False,
    help="Pack Hugging Face models into chunked, self-verifying bundles for air-gapped transfer.",
    no_args_is_help=True,
)


@app.command()
def pack(
    repo_id: Annotated[
        str, typer.Argument(help="Hugging Face repo id, e.g. Qwen/Qwen2.5-7B-Instruct.")
    ],
    dest: Annotated[str, typer.Option("--dest", help="Directory to write the bundle into.")],
    revision: Annotated[
        str, typer.Option("--revision", help="Git revision to pin. Resolved to a commit SHA.")
    ] = "main",
    chunk_size: Annotated[
        str, typer.Option("--chunk-size", help="Max part size (e.g. 3900M, 16G) or 'none'.")
    ] = "3900M",
    include: Annotated[
        list[str] | None, typer.Option("--include", help="fnmatch include pattern (repeatable).")
    ] = None,
    exclude: Annotated[
        list[str] | None, typer.Option("--exclude", help="fnmatch exclude pattern (repeatable).")
    ] = None,
    staging: Annotated[
        str | None,
        typer.Option("--staging", help="Download cache dir. Defaults to ~/.cache/modelferry/."),
    ] = None,
) -> None:
    """Pack a Hugging Face model repo into a bundle (connected side)."""
    raise NotImplementedError("pack lands in Phase 3")


@app.command()
def verify(
    bundle_dir: Annotated[str, typer.Argument(help="Bundle directory to verify.")],
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Print only the summary line and failures.")
    ] = False,
) -> None:
    """Verify a bundle offline against its manifest."""
    raise NotImplementedError("verify lands in Phase 2")


@app.command()
def unpack(
    bundle_dir: Annotated[str, typer.Argument(help="Bundle directory to unpack.")],
    dest_dir: Annotated[
        str, typer.Argument(help="Destination directory for the reconstructed tree.")
    ],
    no_verify: Annotated[
        bool, typer.Option("--no-verify", help="Skip the verify pass before unpacking.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Allow unpacking into a non-empty destination.")
    ] = False,
) -> None:
    """Verify and unpack a bundle offline into a loadable model tree."""
    raise NotImplementedError("unpack lands in Phase 2")


@app.command()
def inspect(
    bundle_dir: Annotated[str, typer.Argument(help="Bundle directory to inspect.")],
) -> None:
    """Print a bundle summary offline. No hashing."""
    raise NotImplementedError("inspect lands in Phase 2")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=version_callback, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    pass


def main() -> None:
    app()


if __name__ == "__main__":
    main()
