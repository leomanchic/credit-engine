"""Package the reviewable source kit plus pinned Windows CPython3.12 wheels."""
from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main():
    entries = {}
    for name in ("src", "docs", "examples", "tests", "scripts", "schemas", "policies"):
        for path in (ROOT / name).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and not any(part.endswith(".egg-info") for part in path.parts):
                entries[path.relative_to(ROOT).as_posix()] = path
    for name in ("pyproject.toml", "MANIFEST.in", "README.md", "MODEL_CARD.md", "THIRD_PARTY_NOTICES",
                 "requirements.lock", ".python-version", ".gitignore", "credit_engine_spec.docx"):
        entries[name] = ROOT / name
    for name in ("credit_engine-2.0.0-py3-none-any.whl", "credit_engine-2.0.0.tar.gz"):
        entries[f"dist/{name}"] = ROOT / "dist" / name
    for path in (ROOT / ".artifacts/wheelhouse").glob("*.whl"):
        entries[f"wheelhouse/{path.name}"] = path
    target = ROOT / "dist/credit_engine-2.0.0-delivery.zip"
    manifest = []
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(entries.items()):
            data = path.read_bytes()
            archive.writestr(f"credit_engine/{name}", data)
            manifest.append(f"{sha256(data).hexdigest()}  {name}")
        archive.writestr("credit_engine/SHA256SUMS", "\n".join(manifest) + "\n")
    checksum = sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(f"{checksum}  {target.name}\n", encoding="utf-8")
    # Verify CRCs and the bytes represented by the manifest before delivery.
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        for line in manifest:
            expected, name = line.split("  ", 1)
            assert sha256(archive.read(f"credit_engine/{name}")).hexdigest() == expected
    print(f"{target.name}: {len(entries)} files; {target.stat().st_size} bytes; SHA256 {checksum}")


if __name__ == "__main__":
    main()
