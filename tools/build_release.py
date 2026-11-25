"""Build the portable Windows x64 ZIP with hash-verified upstream binaries."""

import hashlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = (
    "https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip",
    "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38",
)
WHEELS = (
    (
        "https://files.pythonhosted.org/packages/45/74/bb9161943fb65caf24e4bb04c788f5c7815e6cb78ac595e7f46781033c82/numpy-2.5.5-cp313-cp313-win_amd64.whl",
        "e0608d1585557b68dcb6a70a41654f7536567960afad79d91752d2863b242b68",
    ),
    (
        "https://files.pythonhosted.org/packages/d7/ed/1b16fb66a37e1b9e41370b93b2c614c7da761a5b2a41dc333f7af4639962/pillow-13.0.0-cp313-cp313-win_amd64.whl",
        "9dda0788a9932776a75d8bcc020076edc308ce2508b51f6739a45e3158b3fc42",
    ),
)


def unpack(package, downloads, destination):
    url, expected = package
    archive = downloads / url.rsplit("/", 1)[1]
    with urllib.request.urlopen(url, timeout=90) as response, archive.open("wb") as out:
        shutil.copyfileobj(response, out)
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for {archive.name}")
    with zipfile.ZipFile(archive) as upstream:
        upstream.extractall(destination)


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as temp:
        work = Path(temp)
        app = work / "Wizard101 Bazaar Buyer"
        runtime = app / "runtime"
        unpack(PYTHON, work, runtime)
        for package in WHEELS:
            unpack(package, work, runtime / "packages")
        (runtime / "python313._pth").write_text("python313.zip\n.\npackages\n..\n", encoding="ascii")
        for name in ("Start Bazaar Buyer.cmd", "bazaar_buyer.py", "read-screen.ps1",
                     "bazaar-dialog-title.png", "bazaar-reference.png", "README.md",
                     "tests/iron-listing.png"):
            target = app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        archive = Path(shutil.make_archive(str(work / "Wizard101-Bazaar-Buyer-windows-x64"),
                                           "zip", work, app.name))
        archive.replace(output / archive.name)
        print(output / archive.name)


if __name__ == "__main__":
    build(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist")
