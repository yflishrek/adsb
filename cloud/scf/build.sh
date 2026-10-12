#!/usr/bin/env bash
# Build the Tencent SCF deployment zip.
#
#   bash cloud/scf/build.sh
#
# Ships exactly three flat files (no dependencies, nothing to pip install):
#   index.py           SCF handler entry point
#   scf_collect.py     the collector + Git Data API pusher
#   cloud_collect.py   shared snapshot/persistence layer
#
# Keep this byte-identical to what `scf_collect.py` expects on sys.path.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
stage="$here/.build"
out="$here/scf.zip"

rm -rf "$stage" "$out"
mkdir -p "$stage"

cp "$here/index.py"                     "$stage/"
cp "$root/scripts/scf_collect.py"       "$stage/"
cp "$root/scripts/cloud_collect.py"     "$stage/"

# Never ship a stray .pyc into the zip.
find "$stage" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

if command -v zip >/dev/null 2>&1; then
    ( cd "$stage" && zip -q -r "$out" . )
else
    # Git Bash on Windows often lacks `zip`; Python is guaranteed here.
    python - "$stage" "$out" <<'PY'
import os, sys, zipfile
stage, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(os.listdir(stage)):
        z.write(os.path.join(stage, name), name)
PY
fi

rm -rf "$stage"

echo "built: $out"
ls -l "$out"
echo
echo "contents:"
if command -v unzip >/dev/null 2>&1; then
    unzip -l "$out"
else
    python -c "import zipfile,sys; [print(' ', i.filename, i.file_size)
               for i in zipfile.ZipFile(sys.argv[1]).infolist()]" "$out"
fi
