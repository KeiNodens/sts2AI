"""Check the source publication boundary and refresh/verify its file inventory."""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist", "out", "bin", "obj"}
ALLOWED = {".py", ".md", ".json", ".toml", ".txt", ".yml", ".ps1", ".java", ".cs", ".csproj"}

def source_files():
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if any(part in SKIP or part.endswith(".egg-info") for part in rel.parts):
            continue
        if p.is_symlink():
            raise ValueError(f"Publication must not contain links: {rel}")
        if p.is_file():
            yield p

def inspect():
    hashes = {}
    for p in source_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel == "PUBLIC-SNAPSHOT.json":
            continue
        if p.name not in {".gitignore", ".gitattributes", "LICENSE"} and p.suffix not in ALLOWED:
            raise ValueError(f"Unexpected file type: {rel}")
        if p.name in {"card_kb.json", "card_kb1.json", ".env", ".mcp.json", "NOTES.md", "launch-local.ps1"} or any(part.startswith("decomp_") or part in {"analysis", "cls", "cls_all", "data"} for part in p.relative_to(ROOT).parts):
            raise ValueError(f"Private/generated file in publication: {rel}")
        text = p.read_text(encoding="utf-8")
        if re.search(r"(?i)C:[\\/]Users[\\/]|C:[\\/]dev[\\/]", text):
            raise ValueError(f"Local path in publication: {rel}")
        if re.search(r"7656119\d{10}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", text):
            raise ValueError(f"Identifier/credential pattern in publication: {rel}")
        if p.suffix == ".py":
            ast.parse(text, filename=rel)
        elif p.suffix == ".json":
            json.loads(text)
        hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-manifest", action="store_true")
    args = ap.parse_args()
    hashes = inspect()
    manifest = ROOT / "PUBLIC-SNAPSHOT.json"
    if args.refresh_manifest:
        manifest.write_text(json.dumps({"format": "sts.public-snapshot.v1", "historyIncluded": False, "files": hashes}, indent=2) + "\n", encoding="utf-8", newline="\n")
    elif manifest.exists() and json.loads(manifest.read_text(encoding="utf-8"))["files"] != hashes:
        raise ValueError("Source inventory changed; review the changes and run --refresh-manifest")
    print(f"Publication checks passed: {len(hashes)} source files")

if __name__ == "__main__":
    main()
