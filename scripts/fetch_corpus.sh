#!/usr/bin/env bash
# Fetch the AWS CardDemo reference corpus (Apache-2.0) at a pinned commit.
#
#   CARDDEMO_REF=<sha> bash scripts/fetch_corpus.sh   # override the commit
#
# The clone lives in corpus/carddemo (gitignored). It is data for analysis
# only: nothing from it is ever executed.
set -euo pipefail

REPO_URL="${CARDDEMO_URL:-https://github.com/aws-samples/aws-mainframe-modernization-carddemo.git}"
REF="${CARDDEMO_REF:-59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/corpus/carddemo"

mkdir -p "$DEST"
cd "$DEST"

# autocrlf=false: COBOL is column-sensitive; never let git rewrite line endings.
if [ ! -d .git ]; then
    git -c init.defaultBranch=main init -q
    git config core.autocrlf false
    git remote add origin "$REPO_URL"
fi

git fetch -q --depth 1 origin "$REF"
git -c advice.detachedHead=false checkout -q --force FETCH_HEAD

echo "carddemo @ $(git rev-parse --short HEAD) -> $DEST"
if ! grep -q "Apache License" LICENSE* 2>/dev/null; then
    echo "WARNING: expected an Apache-2.0 LICENSE file in the corpus" >&2
fi
