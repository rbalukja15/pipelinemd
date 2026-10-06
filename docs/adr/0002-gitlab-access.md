# 0002. Read GitLab with the least access that works, and keep writes separate

- **Status:** Accepted
- **Date:** 2026-10-06

## Context

pipelinemd reads other people's pipelines. To do that it needs a GitLab
credential, and the credential it is handed is often far more powerful than
the job needs: a personal access token with the `api` scope can push code,
change settings and delete projects on everything its owner can reach. The
tool runs on laptops and inside CI jobs, sometimes on runners shared between
projects, and it reads logs that can contain anything a build printed.

Three questions had to be settled before the client was built:

1. **What does pipelinemd need to read, and with what scope?** A diagnosis
   needs a pipeline, its jobs (including retried attempts, for flakiness), a
   job's trace, and optionally the `.gitlab-ci.yml` at the failing ref.
   All of that is readable with `read_api`, and inside a job with the job's
   own `CI_JOB_TOKEN`.
2. **What about writes?** The roadmap includes opening a merge request with a
   fix for `yaml`-class failures
   ([#24](https://github.com/rbalukja15/pipelinemd/issues/24)). A tool that
   can write is a much bigger thing to trust than one that reads.
3. **Which server?** Users run gitlab.com and self-managed instances, some
   served from a subdirectory (`https://host/gitlab`), and they paste URLs
   from either.

## Decision

**Reads use the narrowest credential available, and the client can only
read.**

- The required scope is `read_api`. Inside a pipeline, `CI_JOB_TOKEN` is used
  with its own `JOB-TOKEN` header, so a diagnose job needs no token configured
  at all, and what it borrows expires when the job ends. Public projects need
  no token.
- Tokens are looked up in a fixed order: `--token`, `$PIPELINEMD_TOKEN`,
  `$GITLAB_TOKEN`, `$GITLAB_PRIVATE_TOKEN`, `$CI_JOB_TOKEN`. A 401 or 403
  names the scope it needs.
- `gitlab/http.py` issues `GET` requests and nothing else. There is no code
  path in the diagnose flow that could change anything on the server.

**A token is handled as a secret from end to end.**

- It travels only in a request header, never in a URL, so it does not land
  in proxy logs or error messages, which quote the URL.
- pipelinemd prints where a token came from (`$GITLAB_TOKEN`), never the
  token.
- Traces are redacted before they reach the terminal, a report or the model.
  The patterns cover GitLab's token prefixes (`glpat-`, `glrt-`, `gldt-` and
  the rest) among others, because a build that echoes its own credentials is
  common. In CI, the token should also be a masked variable, so GitLab masks
  it in the trace before pipelinemd ever reads it. Redaction is the second
  line, not the first.

**A token stays with the instance it belongs to.** The instance is
`--gitlab-url`, else `$PIPELINEMD_GITLAB_URL`, else `$CI_SERVER_URL` inside a
pipeline, else `https://gitlab.com`. A token from the environment is sent only
when the target's scheme, host and port match that instance. Pasting a URL on
another host gets an unauthenticated request and a note saying the token was
withheld, so a link in a chat message cannot collect someone's `$GITLAB_TOKEN`.
A token passed with `--token` goes where the same command sends it, because
the user named both. An instance in a subdirectory is given with
`--gitlab-url`, which also moves the prefix out of the project path.

**Writes are opt-in, separate, and gated.** Anything that writes to GitLab,
starting with #24's merge request, follows these rules:

- It is off unless a flag turns it on. Running pipelinemd without that flag
  never writes, whatever token it has.
- It uses its own credential, read from its own variable, with the `api`
  scope it needs. The read token is never promoted to a write token, so
  granting read access never implies write access.
- It acts only when a label a maintainer controls is present, so the project
  decides where automation is welcome, not the tool.
- It acts only on a `yaml` failure at high confidence, and only after the
  patched file passes GitLab's CI Lint API
  ([#25](https://github.com/rbalukja15/pipelinemd/issues/25)). The class
  comes from rules and retry history, never from the model
  ([ADR-0001](0001-stack-and-scope.md)).
- It opens a merge request for a person to review. It never pushes to an
  existing branch or merges anything.

The exact flag, variable and label names are #24's to choose; the rules above
are what it has to satisfy.

## Consequences

- A diagnose job in CI works with no setup and no long-lived secret, and the
  worst a leaked job token can do is bounded by the job's lifetime and
  permissions.
- Someone with a token for a self-managed instance has to say which instance
  it is for (`PIPELINEMD_GITLAB_URL` or `--gitlab-url`), or pass it with
  `--token`. Inside a pipeline, `CI_SERVER_URL` already says so. That one line
  of configuration is the price of not sending a credential to whichever host
  a pasted link names.
- Because the read path cannot write, reviewing it for safety means reading
  one HTTP client, not the whole CLI.
- The write path costs a second credential to set up. That friction is
  intended: automated changes should be a decision someone made on purpose.
- Redaction reduces exposure but is not a guarantee. A secret in a shape no
  pattern matches will pass through, which is why masked CI variables remain
  the first defence.
