---
description: Housekeeping - Document, Commit, and Push
---
This workflow performs standard project housekeeping, consisting of updating documentation based on recent changes and committing/pushing the code to version control.

## Step 1: Review Recent Changes
Review the recent Git diffs or implementation plans to understand what has changed in the codebase since the last commit.
```bash
git status
git diff --stat
```

## Step 2: Update Documentation
Use the file editing tools (`replace_file_content` or `multi_replace_file_content`) to update the three core docs in `/docs/`. Each file has a specific role:

### `docs/active_context.md` — "What's happening now"
- Bump the `Last Updated` date
- Update the **Current State** milestone table — mark completed items ✅, add new ones
- Move recently completed work into the **Recently Completed** section
- Update the **Active Goal** if the project's focus has shifted
- Revise **Next Immediate Steps** — delete completed steps, add upcoming ones
- Update **Open Questions** — remove resolved questions, add new unknowns

### `docs/decision_log.md` — "Why we did it that way"
- Add a new `ADR-NNN` section for decisions that change data flow, introduce dependencies, establish patterns, or resolve design-flaw bugs
- Use the standard format: **Date/Status/Deciders → Context → Decision → Consequences**
- Never delete or modify existing ADRs — they're historical records

### `docs/system_architecture.md` — "How the system is built"
- Bump the **Version** number (minor for features, patch for fixes)
- Update the **Architecture Overview** diagram if new components exist
- Keep the **File Tree** accurate — add new files, remove deleted ones
- Add new **Core Development Rules** when a pattern is established
- Update **Admin Utilities** when new admin endpoints are added

### General rules
- Ensure updated documentation matches markdown standards
- Each doc should be accurate if read by someone with zero prior context

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
