# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for issue operations from this repo.

## Conventions

- Create: `gh issue create --title "..." --body-file <file>`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels,comments`, with relevant state and label filters
- Comment: `gh issue comment <number> --body-file <file>`
- Apply or remove a label: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

The GitHub repository is inferred from this clone’s remote.

## Pull requests as a triage surface

**PRs as a request surface: no.**

If this flag is changed to `yes`, triage external PRs with the same labels and states as issues. Use `gh pr view`, `gh pr diff`, `gh pr list`, `gh pr comment`, `gh pr edit`, and `gh pr close`. Resolve an ambiguous `#<number>` as a PR or issue before acting.

## Skill operations

- “Publish to the issue tracker”: create a GitHub issue.
- “Fetch the relevant ticket”: read the GitHub issue and its comments.

## Wayfinding operations

A map is one issue labelled `wayfinder:map`; child issues are its tickets. Link children as GitHub sub-issues where available. Otherwise, list them in the map and add `Part of #<map>` to each child. Label children `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`.

Record blocking with GitHub issue dependencies where available. Otherwise, add `Blocked by: #<n>, #<n>` to the child body. The frontier is the first open, unassigned child in map order whose blockers are closed.

Claim a ticket with `gh issue edit <n> --add-assignee @me` before working. Resolve it by commenting with the answer, closing it, and adding a short linked decision to the map.
