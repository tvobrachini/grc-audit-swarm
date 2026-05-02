# Triage Labels

Label strings for the five canonical triage roles used by the `triage` skill.

| Role | Label string |
|------|-------------|
| Maintainer needs to evaluate | `needs-triage` |
| Waiting on reporter | `needs-info` |
| Fully specified, AFK-agent-ready | `ready-for-agent` |
| Needs human implementation | `ready-for-human` |
| Will not be actioned | `wontfix` |

## Usage

Apply labels via:

```bash
gh issue edit <number> --add-label "<label>"
gh issue edit <number> --remove-label "<label>"
```

## State machine

```
[new issue] → needs-triage
needs-triage → needs-info       (missing reproduction / spec)
needs-triage → ready-for-agent  (fully specified, no human needed)
needs-triage → ready-for-human  (needs human judgment or implementation)
needs-triage → wontfix          (out of scope / won't action)
needs-info   → needs-triage     (reporter replied)
needs-info   → wontfix          (no response after reasonable wait)
```
