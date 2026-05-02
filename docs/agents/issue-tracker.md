# Issue Tracker

This repo uses **GitHub Issues** via the `gh` CLI.

## Key facts

- **Repo**: `tvobrachini/grc-audit-swarm`
- **CLI**: `gh` (GitHub CLI)
- **Remote**: `origin` → `git@github.com:tvobrachini/grc-audit-swarm.git`

## Common operations

```bash
# Create an issue
gh issue create --title "<title>" --body "<body>" --label "<label>"

# List open issues
gh issue list

# View an issue
gh issue view <number>

# Close an issue
gh issue close <number>

# Add a label
gh issue edit <number> --add-label "<label>"

# Remove a label
gh issue edit <number> --remove-label "<label>"
```

## Notes

- Skills that create issues should use `gh issue create` with `--repo tvobrachini/grc-audit-swarm` if run outside the repo root.
- Apply triage labels at creation time where possible (see `triage-labels.md`).
