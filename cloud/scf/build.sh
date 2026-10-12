#!/usr/bin/env bash
# Build the Tencent SCF deployment zip.
#
#   bash cloud/scf/build.sh
#
# Ships exactly four flat files (no dependencies, nothing to pip install):
#   index.py           SCF handler entry point
#   scf_collect.py     the collector + storage backend dispatch
#   cloud_collect.py   shared snapshot/persistence layer
#   cos_client.py      COS uploader (stdlib-only V5 signing)
#
# Keep this byte-identical to what `scf_collect.py` expects on sys.path.
#
# Cleanup and zipping go through Python rather than `rm`/`zip`: some shells shim
# `rm` (which hangs instead of deleting), and Git Bash on Windows may resolve
# `zip` to an unrelated binary on PATH.  Python is the one tool this repo
# already assumes.
set -euo pipefail

PY="${PYTHON:-python}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
stage="$here/.build"
out="$here/scf.zip"

"$PY" - "$stage" "$out" <<'PY'
import os, shutil, sys
for p in sys.argv[1:]:
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)
    elif os.path.exists(p):
        os.remove(p)
PY

mkdir -p "$stage"

cp "$here/index.py"                     "$stage/"
cp "$root/scripts/scf_collect.py"       "$stage/"
cp "$root/scripts/cloud_collect.py"     "$stage/"
cp "$root/scripts/cos_client.py"        "$stage/"

"$PY" - "$stage" "$out" <<'PY'
import os, shutil, sys, zipfile
stage, out = sys.argv[1], sys.argv[2]

# Never ship a stray __pycache__ into the zip.
for dirpath, dirnames, _ in os.walk(stage):
    for d in list(dirnames):
        if d == "__pycache__":
            shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
            dirnames.remove(d)

names = sorted(os.listdir(stage))
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in names:
        z.write(os.path.join(stage, name), name)

print("built:", out, f"({os.path.getsize(out)} bytes)")
print("contents:")
with zipfile.ZipFile(out) as z:
    for i in z.infolist():
        print(f"  {i.filename:22} {i.file_size:8} bytes")
PY

"$PY" - "$stage" <<'PY'
import shutil, sys
shutil.rmtree(sys.argv[1], ignore_errors=True)
PY
