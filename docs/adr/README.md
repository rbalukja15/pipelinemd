# Architecture decision records

Each record captures one decision that shaped pipelinemd: the situation that
forced it, what was chosen, and what that choice costs. They are written for
someone arriving later who wants to know *why* the code looks the way it does,
not just what it does. [Architecture](../architecture.md) describes the
system as it stands; these explain how it got that way.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-stack-and-scope.md) | A GitLab CI failure doctor: a deterministic core with the model at the edge, shipped as a zero-dependency Python CLI | Accepted |
| [0002](0002-gitlab-access.md) | GitLab access: read-only with the narrowest token, tokens kept to their own instance, writes opt-in and gated | Accepted |

## Writing one

Copy [`template.md`](template.md) to `NNNN-short-title.md`, using the next
free number, and add it to the table above.

- **One decision per record.** If it needs an "and", it is probably two.
- **Write the context as it was**, including the options that lost. A
  decision only reads as obvious once the alternatives are left out.
- **Consequences include the costs.** A record with no downside is a sales
  pitch, and the downsides are what a later reader most needs.
- **Records are not edited to match later thinking.** When a decision is
  reversed, write a new record that supersedes it, and set the old one's
  status to `Superseded by NNNN`. The history of a change of mind is part of
  the reasoning.
