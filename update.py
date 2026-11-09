"""Fetch the latest version from GitHub before starting. Never touches .env."""
import io
import urllib.request
import zipfile
from pathlib import Path

URL = "https://github.com/pbz13p/asx-terminal/archive/refs/heads/main.zip"
ROOT = Path(__file__).resolve().parent
KEEP = {".env", "start.bat"}


def main():
    try:
        with urllib.request.urlopen(URL, timeout=20) as r:
            archive = zipfile.ZipFile(io.BytesIO(r.read()))
    except Exception:
        print("Update skipped: GitHub not reachable. Starting current version.")
        return
    count = 0
    for item in archive.infolist():
        rel = Path(*Path(item.filename).parts[1:])
        if item.is_dir() or not rel.parts or ".." in rel.parts or rel.name in KEEP:
            continue
        target = ROOT / rel
        data = archive.read(item)
        if not target.exists() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            count += 1
    print(f"Updated {count} file(s)." if count else "Already up to date.")


if __name__ == "__main__":
    main()
