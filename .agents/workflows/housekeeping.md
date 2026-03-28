---
description: Housekeeping - Document, Commit, and Push
---
This workflow performs standard project housekeeping, consisting of updating documentation based on recent changes and committing/pushing the code to version control.

## Step 1: Review Recent Changes
Review the recent Git diffs or implementation plans to understand what has changed in the codebase since the last commit.
```bash
git status
git diff
```

## Step 2: Update Documentation
Use the file editing tools (`replace_file_content` or `multi_replace_file_content`) to logically apply updates to documentation files (like `README.md`, architectural diagrams, or inline docstrings) so they accurately reflect the new features, architecture changes, schemas, or removed dependencies.
- Ensure that updated documentation matches markdown standards.

// turbo-all
## Step 3: Stage Changes
Once documentation is updated, stage all files for the commit.
```bash
git add .
```

## Step 4: Commit Changes
Create a descriptive commit message that summarizes the core architectural changes, bug fixes, and documentation updates.
```bash
git commit -m "chore: Housekeeping and documentation updates for recent features"
```

## Step 5: Push to Repository
Push the staged and committed changes to the configured remote repository.
```bash
git push
```
