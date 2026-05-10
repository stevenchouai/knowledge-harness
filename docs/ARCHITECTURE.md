# Knowledge Harness Architecture

## What this harness is for

This repo is not meant to be a visible product that the user operates every time.

It is meant to be an invisible runtime layer behind Steven's `digital-twin` workflow.

In Steven's setup:

- the Obsidian vault is the source of truth for knowledge
- the `digital-twin` skill is the user-facing interface
- the harness is the middleware that quietly improves routing, context assembly, logging, and review

## What this harness is not

It is not a heavy benchmark framework.

It is not a hard policy wall that blocks every Codex run.

It is not a global pre-hook that hijacks unrelated coding tasks.

If it becomes noisy, manual, or generic, it is the wrong harness for this user.

## The right direction for Steven

The right direction is a `soft harness` with three properties:

1. `Invisible by default`
  The user should keep invoking `digital-twin` or asking natural-language questions.
   The harness should sit behind that flow, not replace it with a separate ritual.
2. `Scoped activation`
  The harness should activate only for knowledge-base tasks:
  - ingest
  - query
  - lint
  - content derivation from the vault
   It should not automatically wrap ordinary repo coding work.
3. `Governed self-improvement`
  The harness may propose changes to prompts, routing, and workflow structure,
   but the judge should be Steven's `digital-twin`, not a blind auto-tuner.

## Recommended system design

### Layer 1: User interface

The visible interface stays the same:

- `digital-twin` skill
- natural questions in Codex/Cursor
- vault-native prompts and schema

The user should not have to think about the harness.

### Layer 2: Runtime harness

The harness does four quiet jobs:

1. Read the vault contract
  - `AGENTS.md`
  - `PROMPTS.md`
  - `wiki/_index.md`
2. Assemble task-specific context
  - query
  - ingest
  - lint
  - content output
3. Execute Codex with the right scope
  - add vault write permissions only when needed
  - store prompt snapshots and run metadata
  - keep a clean audit trail
4. Observe quality
  - did the result write to the expected location
  - did it cite the right pages
  - did it over-read or under-read
  - did it create a reusable output

### Layer 3: Reflection loop

This is where the harness "grows", but in a controlled way.

After enough runs, the harness should create structured proposals such as:

- prompt-template change
- routing-rule change
- missing concept coverage
- missing output format
- recurring failure mode

Those proposals should be reviewed by `digital-twin`, which decides:

- accept
- reject
- revise

This keeps the system adaptive without turning it into random prompt drift.

## Why not a global pre-hook

A global pre-hook sounds elegant, but it is the wrong first move here.

Reasons:

- Steven uses Codex for many tasks that are not knowledge-base tasks
- a global hook would add friction and side effects to unrelated work
- knowledge workflows benefit from richer context selection than a simple pre-hook can provide
- hooks are better as a narrow entrypoint after the routing logic is already stable

## Best rollout path

### Phase 1: Silent wrapper

Keep the harness repo separate, but make it callable through one stable entrypoint.

Goal:

- the user thinks they are calling `digital-twin`
- the runtime quietly routes through the harness when the task is vault-related

### Phase 2: Shadow evaluation

After each run, store:

- prompt snapshot
- files read
- files written
- final answer
- lightweight quality notes

No auto-edits yet. Just observation.

### Phase 3: Proposal generation

Periodically produce "improvement proposals" in this repo, for example under:

`proposals/`

Each proposal should describe:

- observed pattern
- suspected issue
- suggested change
- expected upside
- risk of the change

### Phase 4: Digital-twin governance

Use the `digital-twin` workflow itself to review and accept those proposals.

That means:

- the harness can evolve
- but evolution stays aligned with Steven's own schema and values

## Concrete recommendation

For this user, the best harness is:

- not benchmark-first
- not hook-first
- not fully autonomous

It should be:

- `digital-twin-first`
- `vault-aware`
- `quiet`
- `reviewable`
- `self-improving through proposals, not blind mutation`

## Next implementation target

The next useful step is:

`Integrate the harness as a vault-only wrapper around digital-twin, then add a shadow review loop.`

That gives Steven the "no-feeling" experience first, and only then adds controlled growth.