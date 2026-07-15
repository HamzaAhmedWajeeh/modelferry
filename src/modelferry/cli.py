"""Typer command-line surface for modelferry (SPEC.md section 3).

`pack` runs the connected-side pack orchestration. `verify`, `unpack`, and
`inspect` are thin wrappers over offline.main, the exact code that ships inside
every bundle, so the installed CLI and the bundled tool behave identically.
"""

from __future__ import annotations

from typing import Annotated

import typer

from . import __version__, offline
from . import pack as pack_mod
from .errors import PackError

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
    try:
        bundle_dir = pack_mod.pack(
            repo_id,
            dest,
            revision=revision,
            chunk_size=chunk_size,
            include=include,
            exclude=exclude,
            staging=staging,
        )
    except PackError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(e.exit_code) from None
    typer.echo(f"wrote bundle: {bundle_dir}")


@app.command()
def verify(
    bundle_dir: Annotated[str, typer.Argument(help="Bundle directory to verify.")],
    quiet: Annotated[
        bool, typer.Option("--quiet", help="Print only the summary line and failures.")
    ] = False,
) -> None:
    """Verify a bundle offline against its manifest."""
    argv = ["verify", bundle_dir] + (["--quiet"] if quiet else [])
    raise typer.Exit(offline.main(argv))


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
    argv = ["unpack", bundle_dir, dest_dir]
    if no_verify:
        argv.append("--no-verify")
    if force:
        argv.append("--force")
    raise typer.Exit(offline.main(argv))


@app.command()
def inspect(
    bundle_dir: Annotated[str, typer.Argument(help="Bundle directory to inspect.")],
) -> None:
    """Print a bundle summary offline. No hashing."""
    raise typer.Exit(offline.main(["inspect", bundle_dir]))


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
