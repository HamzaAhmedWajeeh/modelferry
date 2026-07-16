"""Dest validation and pre-download free-space checks (no network).

These guard the tool's likeliest real failure: --dest is usually removable media,
so an unmounted stick or wrong drive letter must fail up front, before a
multi-gigabyte download, not after it. disk_usage is monkeypatched so the checks
run offline with scripted numbers.
"""

from collections import namedtuple

import pytest

from modelferry import pack
from modelferry.errors import LocalFsError

Usage = namedtuple("Usage", "total used free")


# --- _validate_dest ---------------------------------------------------------- #


def test_validate_dest_creates_missing(tmp_path):
    d = tmp_path / "sub" / "dest"
    assert not d.exists()
    out = pack._validate_dest(str(d))
    assert d.is_dir()
    assert out == str(d)


def test_validate_dest_rejects_non_directory(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    with pytest.raises(LocalFsError) as excinfo:
        pack._validate_dest(str(f))
    assert excinfo.value.exit_code == 4
    assert str(f) in str(excinfo.value)
    assert "not a directory" in str(excinfo.value)


def test_validate_dest_rejects_unwritable(tmp_path, monkeypatch):
    d = tmp_path / "dest"
    d.mkdir()

    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(pack.tempfile, "mkstemp", boom)
    with pytest.raises(LocalFsError) as excinfo:
        pack._validate_dest(str(d))
    assert excinfo.value.exit_code == 4
    assert "not writable" in str(excinfo.value)
    assert str(d) in str(excinfo.value)


# --- _check_free_space ------------------------------------------------------- #


def test_free_space_same_volume_insufficient(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    dest = tmp_path / "dest"
    stage.mkdir()
    dest.mkdir()
    monkeypatch.setattr(pack, "_same_volume", lambda a, b: True)
    monkeypatch.setattr(pack.shutil, "disk_usage", lambda p: Usage(1000, 0, 100))
    # total 60 -> needs 120 on the shared volume, only 100 free.
    with pytest.raises(LocalFsError) as excinfo:
        pack._check_free_space(60, str(stage), str(dest))
    msg = str(excinfo.value)
    assert excinfo.value.exit_code == 4
    assert "120" in msg  # bytes needed (twice the total)
    assert "100" in msg  # bytes available
    assert str(dest) in msg


def test_free_space_same_volume_sufficient(tmp_path, monkeypatch):
    monkeypatch.setattr(pack, "_same_volume", lambda a, b: True)
    monkeypatch.setattr(pack.shutil, "disk_usage", lambda p: Usage(10_000, 0, 10_000))
    # 2 * 60 = 120 <= 10000: no raise.
    pack._check_free_space(60, str(tmp_path / "s"), str(tmp_path / "d"))


def test_free_space_diff_volume_dest_short(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    dest = tmp_path / "dest"
    stage.mkdir()
    dest.mkdir()
    monkeypatch.setattr(pack, "_same_volume", lambda a, b: False)

    def usage(p):
        return Usage(10_000, 0, 10_000) if "stage" in str(p) else Usage(50, 0, 50)

    monkeypatch.setattr(pack.shutil, "disk_usage", usage)
    with pytest.raises(LocalFsError) as excinfo:
        pack._check_free_space(100, str(stage), str(dest))
    msg = str(excinfo.value)
    assert excinfo.value.exit_code == 4
    assert str(dest) in msg
    assert "100" in msg and "50" in msg


def test_free_space_diff_volume_staging_short(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    dest = tmp_path / "dest"
    stage.mkdir()
    dest.mkdir()
    monkeypatch.setattr(pack, "_same_volume", lambda a, b: False)

    def usage(p):
        return Usage(50, 0, 50) if "stage" in str(p) else Usage(10_000, 0, 10_000)

    monkeypatch.setattr(pack.shutil, "disk_usage", usage)
    with pytest.raises(LocalFsError) as excinfo:
        pack._check_free_space(100, str(stage), str(dest))
    msg = str(excinfo.value)
    assert excinfo.value.exit_code == 4
    assert str(stage) in msg


# --- ordering inside pack(): both gates run before the download -------------- #


def test_pack_validates_dest_before_resolve(tmp_path, monkeypatch):
    import modelferry.hf as hf

    def no_resolve(*a, **k):
        raise AssertionError("resolve must not run when --dest is invalid")

    monkeypatch.setattr(hf, "resolve", no_resolve)
    bad = tmp_path / "afile"
    bad.write_text("x")  # a file, not a directory
    with pytest.raises(LocalFsError) as excinfo:
        pack.pack("acme/model", str(bad))
    assert excinfo.value.exit_code == 4


def test_pack_checks_space_before_download(tmp_path, monkeypatch):
    import modelferry.hf as hf

    stage = tmp_path / "stage"
    stage.mkdir()
    dest = tmp_path / "dest"

    def fake_resolve(*a, **k):
        return {
            "commit_sha": "a" * 40,
            "source": {"repo_id": "acme/model", "commit_sha": "a" * 40},
            "files": [("model.bin", 100)],
            "total_bytes": 100,
            "local_dir": str(stage),
            "endpoint": "https://huggingface.co",
        }

    def no_download(*a, **k):
        raise AssertionError("download must not run when free space is short")

    monkeypatch.setattr(hf, "resolve", fake_resolve)
    monkeypatch.setattr(hf, "download", no_download)
    monkeypatch.setattr(pack, "_same_volume", lambda a, b: True)
    monkeypatch.setattr(pack.shutil, "disk_usage", lambda p: Usage(50, 0, 50))
    with pytest.raises(LocalFsError) as excinfo:
        pack.pack("acme/model", str(dest))
    assert excinfo.value.exit_code == 4
