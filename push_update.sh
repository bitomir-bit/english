#!/bin/bash
# Daily cron: regenerate phrases.json and push to GitHub
set -e

REPO="/Users/mykhailoglodian/Downloads/claude_projects/english"
source "$REPO/.env"
REMOTE="https://bitomir-bit:${GITHUB_TOKEN}@github.com/bitomir-bit/english.git"

cd "$REPO"
python3 generate_phrases.py

git add phrases.json
if git diff --cached --quiet; then
  echo "No changes — $(date)"
  exit 0
fi

COUNT=$(python3 -c "import json; print(len(json.load(open('phrases.json'))))")
git commit -m "Update phrases — $(date '+%Y-%m-%d')"
git push "$REMOTE" main

osascript -e "display notification \"$COUNT phrases now live on your app 📱\" with title \"English App Updated\" sound name \"Glass\""
echo "Pushed $COUNT phrases — $(date)"
