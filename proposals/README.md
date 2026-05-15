# Proposal convention

`proposals/` stores public-safe improvement proposals for the harness. A
proposal records an observed pattern and a possible change, but it is not
approval to mutate the vault, prompts, routing, or code.

## Filename format

Use:

```text
YYYY-MM-DD-<short-kebab-case-summary>.md
```

Rules:

- Use the date the proposal is written.
- Keep the summary lowercase, ASCII, and separated with hyphens.
- Describe the harness concern, not private vault content.
- Do not include private paths, account IDs, tokens, employer/client facts,
  Feishu IDs, or named third-party learning cards.

Example:

```text
2026-05-12-query-citation-gap.md
```

## Required sections

Every proposal must include these sections:

```markdown
# Query citation gap

## Observed pattern

Sanitized observation of repeated behavior, using no private excerpts.

## Suspected issue

Why the pattern may indicate a harness, prompt, routing, or review problem.

## Suggested change

The smallest public-safe change that could address the issue.

## Upside

What improves if the proposal is accepted.

## Risk

What could go wrong, including false positives, extra process, or maintenance
cost.
```

## Review flow

1. An agent writes a proposal in this directory using sanitized evidence only.
2. Steven or the `digital-twin` workflow reviews the proposal and decides
   whether to accept, reject, or revise it.
3. Implementation happens only after review. Accepted proposals should become
   normal scoped tasks with tests or validation steps appropriate to the change.
4. Rejected or superseded proposals may stay as historical notes, but they
   should not be treated as active instructions.
