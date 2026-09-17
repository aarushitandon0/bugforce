"""Regenerates the fixtures for lib/tar.test.ts and lib/traceback.test.ts.

    python web/lib/__fixtures__/make_fixtures.py [selection.json]

The tarballs are written by Python's own tarfile module in all three of its
formats, because that is what fn_persist ships to the browser. The expected
listing is written alongside by Python too, so the TypeScript untar is checked
against tarfile's reading of the same bytes, not against itself.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

FILES = {
    "challenge/pkg/__init__.py": b"",
    "challenge/pkg/retry.py": b"def retry(n):\n    return n < 3\n",
    "challenge/pkg/windows.py": b"x = 1\r\ny = 2\r\n",
    "challenge/docs/naïve-ünicøde.txt".encode().decode(): "café\n".encode(),
    "challenge/" + "deeply/" * 16 + "nested_module_with_a_long_name.py": b"VALUE = 42\n",
    "challenge/exact_block.bin": bytes(range(256)) * 2,
    "challenge/one_over_block.txt": b"a" * 513,
    "challenge/traceback.txt": b"tests/test_x.py:3: AssertionError\n",
    "challenge/.git/HEAD": b"ref: refs/heads/main\n",
}


def build(fmt: int) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=fmt) as tar:
        for directory in ("challenge", "challenge/pkg"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for name, data in FILES.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("challenge/link.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "pkg/retry.py"
        tar.addfile(link)
    return buffer.getvalue()


def expected(archive: bytes) -> list[dict]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        return [
            {"path": m.name, "size": m.size, "sha256": hashlib.sha256(tar.extractfile(m).read()).hexdigest()}
            for m in tar.getmembers()
            if m.isfile()
        ]


def main() -> None:
    for label, fmt in (("pax", tarfile.PAX_FORMAT), ("gnu", tarfile.GNU_FORMAT)):
        archive = build(fmt)
        (HERE / f"tree-{label}.tar.gz").write_bytes(archive)
        (HERE / f"tree-{label}.expected.json").write_text(json.dumps(expected(archive), indent=2), encoding="utf-8")

    # USTAR can't hold non-ASCII names or paths over 255 bytes, but it does
    # split 101-255 byte paths across the prefix field -- test that separately.
    ustar_files = {k: v for k, v in FILES.items() if k.isascii() and len(k) <= 255}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in ustar_files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    (HERE / "tree-ustar.tar.gz").write_bytes(buffer.getvalue())
    (HERE / "tree-ustar.expected.json").write_text(
        json.dumps(expected(buffer.getvalue()), indent=2), encoding="utf-8"
    )

    if len(sys.argv) > 1:
        selection = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        wanted = {
            ("tenacity/asyncio/retry.py", 81): "traceback-async-exc.txt",
            ("tenacity/retry.py", 45): "traceback-assert-instance.txt",
        }
        for record in selection["results"]:
            key = (record["site"]["path"], record["site"]["lineno"])
            if record["outcome"] == "ADMITTED" and key in wanted:
                (HERE / wanted[key]).write_text(record["traceback"], encoding="utf-8")


if __name__ == "__main__":
    main()
