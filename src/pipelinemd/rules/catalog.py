"""The rule catalog: signatures for CI failures that have a known answer.

Every entry here is a failure mode that does not need a language model to
recognise. A rule is deliberately narrow - it matches a specific message that
a specific tool prints - and pairs it with the fix a maintainer would give.
Rules that cannot be sure say so through ``confidence``.

Adding a rule: keep ``patterns`` anchored to text the tool actually emits (not
paraphrases), write ``explanation`` as *why this happens*, and make each entry
of ``fixes`` an action someone can take.
"""

from __future__ import annotations

from ..models import Category, Confidence, Rule

_CI_DOCS = "https://docs.gitlab.com/ci/"
_YAML_DOCS = "https://docs.gitlab.com/ci/yaml/"
_RUNNER_DOCS = "https://docs.gitlab.com/ci/runners/"
_DOCKER_DOCS = "https://docs.gitlab.com/ci/docker/using_docker_build/"
_CACHE_DOCS = "https://docs.gitlab.com/ci/caching/"
_ARTIFACT_DOCS = "https://docs.gitlab.com/ci/jobs/job_artifacts/"

RUNNER_RULES: tuple[Rule, ...] = (
    Rule(
        id="runner.none-available",
        title="No runner available to pick up the job",
        category=Category.RUNNER,
        patterns=(
            r"[Tt]his job is stuck",
            r"don't have any (?:active |online )?runners",
            r"no (?:active )?runners? (?:online|available)",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The job was queued but no runner matched it. Either no runner is online for "
            "the project, or every online runner's tags fail to cover the job's `tags:`."
        ),
        fixes=(
            "Check Settings → CI/CD → Runners: at least one runner must be online (green).",
            "Compare the job's `tags:` against the runners' tags - a job with a tag only "
            "runs on a runner carrying that exact tag.",
            "If the job has no `tags:`, the runner must have 'Run untagged jobs' enabled.",
            "For group/instance runners, confirm the project has shared runners enabled.",
        ),
        docs=(_RUNNER_DOCS,),
    ),
    Rule(
        id="runner.job-timeout",
        title="Job exceeded its timeout",
        category=Category.RUNNER,
        patterns=(
            r"execution took longer than \S+ seconds",
            r"ERROR: Job failed: execution took longer than",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The runner killed the job because it ran past the configured timeout. The "
            "effective limit is the smaller of the project's CI/CD timeout, the job's own "
            "`timeout:`, and the runner's maximum job timeout."
        ),
        fixes=(
            "Find which step hung - compare section durations in the distilled output.",
            "Raise `timeout:` on the job, or the project timeout in Settings → CI/CD.",
            "A runner's 'Maximum job timeout' overrides a longer project timeout; check it too.",
            "If a step waits on input, add a non-interactive flag (`-y`, `--no-input`, `CI=true`).",
        ),
        docs=(_YAML_DOCS,),
    ),
    Rule(
        id="runner.system-failure",
        title="Runner system failure (the job never ran properly)",
        category=Category.RUNNER,
        patterns=(r"ERROR: Job failed \(system failure\)",),
        confidence=Confidence.HIGH,
        explanation=(
            "gitlab-runner failed while preparing or cleaning up the environment, not while "
            "running your script. The cause is infrastructure: the executor could not start a "
            "container, lost its connection, or ran out of a host resource."
        ),
        fixes=(
            "Read the line right before this one - it names the underlying failure.",
            "Retry the job; genuine system failures are often transient.",
            "Add `retry: { max: 2, when: runner_system_failure }` to absorb the flaky case.",
            "If it repeats on one runner, check that runner's host: disk, memory, docker daemon.",
        ),
        docs=(_RUNNER_DOCS,),
    ),
    Rule(
        id="runner.log-limit",
        title="Job log exceeded the size limit and was truncated",
        category=Category.RUNNER,
        patterns=(r"Job's log exceeded limit of \d+ bytes", r"log limit exceeded"),
        confidence=Confidence.HIGH,
        explanation=(
            "The job produced more log output than the instance allows, so the trace was cut "
            "off. The real failure may be in the discarded part."
        ),
        fixes=(
            "Quiet the noisy step: `npm ci --silent`, `pip install -q`, `make -s`.",
            "Redirect verbose output to a file and expose it as an artifact instead.",
            "Drop `set -x` / `--verbose` / `--debug` flags left over from an earlier debug session.",
        ),
    ),
    Rule(
        id="runner.no-space",
        title="Runner ran out of disk space",
        category=Category.RESOURCES,
        patterns=(
            r"no space left on device",
            r"\bENOSPC\b",
            r"[Dd]isk quota exceeded",
            r"write error: No space left",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The filesystem the job writes to is full. On shared runners this is usually "
            "accumulated Docker layers, caches and old build directories rather than your job "
            "genuinely needing that much space."
        ),
        fixes=(
            "On a self-managed runner host: `docker system prune -af --volumes` and check `df -h`.",
            "Cap the runner's cache/build growth via `builds_dir` cleanup or a periodic prune job.",
            "Shrink what the job writes: shallow clone (`GIT_DEPTH`), narrower `cache: paths`.",
            "Set `DOCKER_DRIVER=overlay2` for docker-in-docker builds, which is far more space-efficient.",
        ),
        docs=(_RUNNER_DOCS,),
    ),
    Rule(
        id="runner.oom-killed",
        title="A process was killed for using too much memory",
        category=Category.RESOURCES,
        patterns=(
            r"\bOOMKilled\b",
            r"Out of memory: Kill(?:ed)? process",
            r"^Killed$",
            r"signal: killed",
            r"Container killed on request. Exit code is 137",
        ),
        confidence=Confidence.HIGH,
        exit_codes=(137,),
        explanation=(
            "The kernel's OOM killer terminated the process. Exit code 137 is 128+9 (SIGKILL) "
            "and is the usual fingerprint. The job did not fail on its own logic - it was killed."
        ),
        fixes=(
            "Raise the memory limit on the runner or the job's container/service.",
            "Reduce peak usage: run tests in fewer parallel workers (`--maxWorkers`, `-n`, `-j`).",
            "For Node builds, cap the heap explicitly: `NODE_OPTIONS=--max-old-space-size=3072`.",
            "Split the job so a single step holds less in memory at once.",
        ),
    ),
    Rule(
        id="runner.canceled",
        title="Job was canceled or received SIGTERM",
        category=Category.RUNNER,
        patterns=(
            r"ERROR: Job (?:canceled|cancelled)",
            r"got signal: terminated",
            r"WARNING: Received SIGTERM",
        ),
        confidence=Confidence.MEDIUM,
        exit_codes=(143,),
        explanation=(
            "The job was stopped from outside: a user cancelled it, a newer pipeline superseded "
            "it via interruptible/auto-cancel, or the runner shut down mid-job."
        ),
        fixes=(
            "If this was auto-cancel, it is expected - a newer commit superseded the pipeline.",
            "Set `interruptible: false` on jobs that must not be auto-cancelled (deploys).",
            "If the runner restarted, check for autoscaling or a host reboot at that timestamp.",
        ),
    ),
)

DOCKER_RULES: tuple[Rule, ...] = (
    Rule(
        id="docker.daemon-unreachable",
        title="Cannot reach the Docker daemon",
        category=Category.DOCKER,
        patterns=(
            r"Cannot connect to the Docker daemon at",
            r"Is the docker daemon running",
            r"docker: Cannot connect to the Docker daemon",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The job ran a `docker` command but no daemon was listening. In GitLab CI that "
            "almost always means the `docker:dind` service is missing, or `DOCKER_HOST` does not "
            "point at it."
        ),
        fixes=(
            'Add the service:  `services: ["docker:dind"]` alongside a `docker:cli` image.',
            "Set `DOCKER_HOST: tcp://docker:2376` (TLS) or `tcp://docker:2375` (no TLS).",
            'With TLS, also set `DOCKER_TLS_CERTDIR: "/certs"` and `DOCKER_CERT_PATH: "/certs/client"`.',
            "On a shell-executor runner, the user running gitlab-runner must be in the `docker` group.",
        ),
        docs=(_DOCKER_DOCS,),
    ),
    Rule(
        id="docker.dind-tls",
        title="docker-in-docker TLS handshake failed",
        category=Category.DOCKER,
        patterns=(
            r"error during connect.*docker:2376",
            r"tls: first record does not look like a TLS handshake",
            r"http: server gave HTTP response to HTTPS client",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The client and the dind service disagree about TLS. Since Docker 19.03 dind "
            "enables TLS by default on port 2376; talking plain HTTP to it (or TLS to 2375) "
            "fails exactly this way."
        ),
        fixes=(
            'Consistent TLS setup: `DOCKER_TLS_CERTDIR: "/certs"`, `DOCKER_HOST: tcp://docker:2376`, '
            '`DOCKER_CERT_PATH: "/certs/client"`, `DOCKER_TLS_VERIFY: 1`.',
            'Or disable TLS on both sides: `DOCKER_TLS_CERTDIR: ""` with `DOCKER_HOST: tcp://docker:2375`.',
            "Do not mix: an empty `DOCKER_TLS_CERTDIR` with port 2376 is the most common mistake.",
        ),
        docs=(_DOCKER_DOCS,),
    ),
    Rule(
        id="docker.pull-denied",
        title="Not allowed to pull the image",
        category=Category.AUTH,
        patterns=(
            r"pull access denied for",
            r"repository does not exist or may require 'docker login'",
            r"ERROR: Preparation failed: failed to pull image",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The registry refused the pull. Either the image is private and the job never "
            "authenticated, or the name/tag is wrong and the registry reports it as a "
            "permission problem (registries do this to avoid leaking which images exist)."
        ),
        fixes=(
            "Double-check the image name and tag first - a typo presents as 'access denied'.",
            "For the GitLab registry, use the built-in job token: "
            "`docker login -u gitlab-ci-token -p $CI_JOB_TOKEN $CI_REGISTRY`.",
            "For a private third-party registry, set the `DOCKER_AUTH_CONFIG` CI variable.",
            "Confirm the token/deploy key still has `read_registry` scope and has not expired.",
        ),
        docs=(_DOCKER_DOCS,),
    ),
    Rule(
        id="docker.push-denied",
        title="Not allowed to push to the registry",
        category=Category.AUTH,
        patterns=(
            r"denied: requested access to the resource is denied",
            r"unauthorized: authentication required",
            r"denied: permission_denied",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The push was rejected for lack of write permission. The job either did not log in, "
            "logged in with a read-only credential, or is pushing to a path the credential does "
            "not cover."
        ),
        fixes=(
            "Log in before pushing: `docker login -u gitlab-ci-token -p $CI_JOB_TOKEN $CI_REGISTRY`.",
            "Tag under the project's own registry path: `$CI_REGISTRY_IMAGE/name:tag`.",
            "`CI_JOB_TOKEN` cannot push to another project's registry - use a deploy token "
            "with `write_registry`.",
            "Check the token has not expired and the project's Container Registry is enabled.",
        ),
        docs=(_DOCKER_DOCS,),
    ),
    Rule(
        id="docker.manifest-unknown",
        title="Image tag does not exist",
        category=Category.DOCKER,
        patterns=(
            r"manifest unknown",
            r"manifest for \S+ not found",
            r"reference does not exist",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The registry has the repository but not the tag or digest requested. Common after "
            "a tag is deleted, a cleanup policy runs, or a variable expands to an empty string "
            "leaving a name like `myimage:`."
        ),
        fixes=(
            "Print the resolved image name before pulling - an unset variable yields a bare tag.",
            "List available tags and pick one that exists.",
            "If a cleanup policy removed it, pin to a digest or a tag the policy keeps.",
        ),
    ),
    Rule(
        id="docker.rate-limit",
        title="Docker Hub pull rate limit reached",
        category=Category.NETWORK,
        patterns=(
            r"toomanyrequests: You have reached your pull rate limit",
            r"You have reached your pull rate limit",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "Docker Hub throttles anonymous pulls per IP. On shared runners many projects "
            "share an egress IP, so the quota is often already spent by someone else."
        ),
        fixes=(
            "Authenticate to Docker Hub in CI - even a free account raises the limit substantially.",
            "Mirror the images you depend on into the GitLab container registry and pull from there.",
            "Configure a pull-through cache/registry mirror on the runner host.",
        ),
    ),
    Rule(
        id="docker.exec-format",
        title="Image architecture does not match the runner",
        category=Category.DOCKER,
        patterns=(
            r"exec format error",
            r"no matching manifest for linux/\S+",
            r"image .*platform .* does not match",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The image was built for a different CPU architecture than the runner - typically an "
            "arm64 image (built on an Apple Silicon laptop) running on an amd64 runner."
        ),
        fixes=(
            "Build multi-arch images with buildx: `--platform linux/amd64,linux/arm64`.",
            "Or pin the build to the runner's architecture: `docker build --platform linux/amd64`.",
            "Check whether the base image publishes a manifest for the runner's architecture at all.",
        ),
    ),
)

GIT_RULES: tuple[Rule, ...] = (
    Rule(
        id="git.auth-failed",
        title="Git authentication failed",
        category=Category.AUTH,
        patterns=(
            r"fatal: Authentication failed for",
            r"could not read Username for '",
            r"fatal: could not read Password for",
            r"HTTP Basic: Access denied",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "A git operation could not authenticate. Inside CI this is nearly always a "
            "credential the job supplies itself - a submodule over HTTPS, a private package "
            "repo, or a push using a token that expired or lacks scope."
        ),
        fixes=(
            "If it is a submodule, prefer relative URLs in `.gitmodules` so `CI_JOB_TOKEN` covers them, "
            "and set `GIT_SUBMODULE_STRATEGY: recursive`.",
            "For cross-project fetches, use a project access token or deploy token with `read_repository`.",
            "Check the token's expiry date - GitLab tokens now expire by default.",
            "To push from CI, use a project access token; `CI_JOB_TOKEN` cannot push to the repo.",
        ),
    ),
    Rule(
        id="git.shallow-depth",
        title="Shallow clone is too shallow for this operation",
        category=Category.GIT,
        patterns=(
            r"fatal: reference is not a tree",
            r"did not send all necessary objects",
            r"fatal: (?:bad object|no merge base)",
            r"unable to read tree",
        ),
        confidence=Confidence.MEDIUM,
        explanation=(
            "GitLab clones with `GIT_DEPTH=20` by default. Anything that reaches further back - "
            "`git describe`, diffing against the default branch, a changelog generator, "
            "`git merge-base` - fails because those commits were never fetched."
        ),
        fixes=(
            "Raise the depth for this job: `variables: { GIT_DEPTH: 0 }` fetches full history.",
            "Or fetch just what you need: `git fetch --deepen=100` / `git fetch origin $CI_DEFAULT_BRANCH`.",
            "For tag-based versioning, also fetch tags: `git fetch --tags`.",
        ),
        docs=(_YAML_DOCS,),
    ),
    Rule(
        id="git.lfs-missing",
        title="Git LFS is not installed in the job image",
        category=Category.GIT,
        patterns=(
            r"git-lfs: command not found",
            r"'git-lfs' was not found",
            r"git: 'lfs' is not a git command",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The repository stores files in LFS but the job's image has no `git-lfs` binary, so "
            "the checkout contains pointer files instead of real content."
        ),
        fixes=(
            "Install it in a `before_script`: `apt-get update && apt-get install -y git-lfs` "
            "(or `apk add git-lfs`), then `git lfs install`.",
            "Or use an image that already bundles git-lfs.",
            "If the job does not need the LFS content, set `GIT_LFS_SKIP_SMUDGE: 1`.",
        ),
    ),
    Rule(
        id="git.lfs-quota",
        title="Git LFS storage quota exceeded",
        category=Category.GIT,
        patterns=(
            r"This repository is over its data quota",
            r"batch response: .*quota",
        ),
        confidence=Confidence.HIGH,
        explanation="The namespace has used all its LFS storage or bandwidth allowance.",
        fixes=(
            "Purge unreferenced LFS objects, or buy additional storage for the namespace.",
            "As a stop-gap, set `GIT_LFS_SKIP_SMUDGE: 1` for jobs that do not need LFS content.",
        ),
    ),
    Rule(
        id="git.submodule-failed",
        title="Submodule checkout failed",
        category=Category.GIT,
        patterns=(
            r"Failed to (?:clone|fetch) .*submodule",
            r"fatal: (?:clone of|repository) .* into submodule path",
            r"errors? during submodule",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The runner could not check out a submodule - usually because the submodule URL is "
            "absolute and the job token does not grant access to that other project."
        ),
        fixes=(
            "Use relative URLs in `.gitmodules` (`../../group/project.git`) so `CI_JOB_TOKEN` applies.",
            "Set `GIT_SUBMODULE_STRATEGY: recursive` (or `normal`) explicitly.",
            "For a submodule in another project, add that project to the job token allowlist "
            "(Settings → CI/CD → Job token permissions).",
        ),
    ),
)

NODE_RULES: tuple[Rule, ...] = (
    Rule(
        id="npm.eresolve",
        title="npm cannot resolve the dependency tree",
        category=Category.DEPENDENCY,
        patterns=(
            r"ERESOLVE unable to resolve dependency tree",
            r"npm ERR! code ERESOLVE",
            r"ERESOLVE could not resolve",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "Two packages demand incompatible versions of a shared peer dependency. npm 7+ "
            "enforces peer dependencies strictly, so a tree that installed fine on npm 6 can "
            "fail here."
        ),
        fixes=(
            "Read the 'Found:' / 'Could not resolve dependency:' block - it names the exact conflict.",
            "Upgrade the package whose peer range is stale, which is the real fix.",
            "Pin the shared dependency with `overrides` in package.json to force one version.",
            "`--legacy-peer-deps` unblocks the build but hides a real incompatibility - use it knowingly.",
        ),
    ),
    Rule(
        id="npm.lockfile-out-of-sync",
        title="package-lock.json is out of sync with package.json",
        category=Category.DEPENDENCY,
        patterns=(
            r"can only install packages when your package\.json and package-lock\.json",
            r"npm ci` can only install",
            r"Missing: \S+ from lock file",
            r"Invalid: lock file's \S+ does not satisfy",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "`npm ci` refuses to guess. Someone edited package.json (or merged a branch) without "
            "regenerating the lockfile, so the two disagree about what should be installed."
        ),
        fixes=(
            "Run `npm install` locally, commit the updated `package-lock.json`, and push.",
            "After a merge that touched dependencies, regenerate the lockfile rather than "
            "hand-resolving its conflicts.",
            "Keep `npm ci` in CI - swapping to `npm install` hides the drift instead of fixing it.",
        ),
    ),
    Rule(
        id="npm.registry-404",
        title="Package not found in the registry",
        category=Category.DEPENDENCY,
        patterns=(
            r"npm ERR! code E404",
            r"npm ERR! 404 Not Found - GET",
            r"404 Not Found - GET \S+",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The registry has no such package or version. Either the name is misspelled, the "
            "version was unpublished, or it is a private package and the job is querying the "
            "public registry."
        ),
        fixes=(
            "Check the exact package name and version in the error line.",
            "For a private scope, configure the registry: "
            "`npm config set @scope:registry https://gitlab.example.com/api/v4/projects/$CI_PROJECT_ID/packages/npm/`.",
            "Add auth for that registry with `CI_JOB_TOKEN` in `.npmrc`.",
        ),
    ),
    Rule(
        id="npm.registry-auth",
        title="npm registry rejected the credentials",
        category=Category.AUTH,
        patterns=(
            r"npm ERR! code E401",
            r"npm ERR! code E403",
            r"Incorrect or missing password",
            r"npm ERR! 401 Unauthorized",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The registry answered 401/403. The job either sent no token, sent an expired one, "
            "or the `.npmrc` auth line does not match the registry host it is talking to."
        ),
        fixes=(
            "Write `.npmrc` in CI: `//gitlab.example.com/api/v4/projects/${CI_PROJECT_ID}/packages/npm/:_authToken=${CI_JOB_TOKEN}`.",
            "The auth line's host and path must match the registry line exactly, including the trailing slash.",
            "Verify the token has `read_package_registry` (and `write_package_registry` to publish).",
        ),
    ),
    Rule(
        id="npm.bad-engine",
        title="Node version does not satisfy the package's engines field",
        category=Category.DEPENDENCY,
        patterns=(
            r"npm ERR! code EBADENGINE",
            r"Unsupported engine",
            r"engine \"node\" is incompatible",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "A dependency declares an `engines.node` range that the job's Node version does not "
            "satisfy. The error line prints both the required and the actual version."
        ),
        fixes=(
            "Pin the job to a matching Node image, e.g. `image: node:20`.",
            "Keep the CI image and the project's `.nvmrc` / `engines` field in step.",
            "If the constraint is wrong, upgrade or replace the offending dependency.",
        ),
    ),
    Rule(
        id="node.heap-oom",
        title="Node ran out of heap memory",
        category=Category.RESOURCES,
        patterns=(
            r"JavaScript heap out of memory",
            r"FATAL ERROR: .*Allocation failed",
            r"Reached heap limit Allocation failed",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "V8 hit its heap ceiling. Large TypeScript projects, source maps and bundlers are "
            "the usual causes; the default limit is well below the container's memory."
        ),
        fixes=(
            'Raise the heap: `NODE_OPTIONS: "--max-old-space-size=4096"` (keep it under the container limit).',
            "Ensure the container actually has that much memory available.",
            "Reduce peak usage: disable source maps in CI, or build packages sequentially.",
            "For Jest, lower `--maxWorkers` - each worker is a separate heap.",
        ),
    ),
    Rule(
        id="yarn.frozen-lockfile",
        title="yarn.lock is out of date",
        category=Category.DEPENDENCY,
        patterns=(
            r"Your lockfile needs to be updated",
            r"--frozen-lockfile",
            r"YN0028",
            r"The lockfile would have been (?:created|modified)",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "Yarn was run with `--frozen-lockfile` (or `--immutable`) and found that installing "
            "would change `yarn.lock` - meaning the lockfile does not match package.json."
        ),
        fixes=(
            "Run `yarn install` locally and commit the updated `yarn.lock`.",
            "Regenerate the lockfile after any merge that touched dependencies.",
        ),
    ),
    Rule(
        id="pnpm.outdated-lockfile",
        title="pnpm-lock.yaml is out of date",
        category=Category.DEPENDENCY,
        patterns=(r"ERR_PNPM_OUTDATED_LOCKFILE", r"Cannot install with \"frozen-lockfile\""),
        confidence=Confidence.HIGH,
        explanation=(
            "pnpm runs with `--frozen-lockfile` in CI by default and found package.json and "
            "pnpm-lock.yaml disagree."
        ),
        fixes=(
            "Run `pnpm install` locally and commit `pnpm-lock.yaml`.",
            "In a workspace, make sure every package's manifest change is reflected in the single root lockfile.",
        ),
    ),
)

PYTHON_RULES: tuple[Rule, ...] = (
    Rule(
        id="pip.no-matching-distribution",
        title="No installable version of a requirement",
        category=Category.DEPENDENCY,
        patterns=(
            r"Could not find a version that satisfies the requirement",
            r"No matching distribution found for",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "pip found no distribution matching the requirement for this interpreter. Usually "
            "the pin does not exist, or no wheel is published for the job's Python version or "
            "platform (a frequent surprise right after a new Python release)."
        ),
        fixes=(
            "Check the version pin against the versions the error lists as available.",
            "Match the job's Python version to one the package supports (`image: python:3.12`).",
            "For a private index, pass `--index-url` / `--extra-index-url` with credentials.",
            "If it is a source-only package, install build tooling first (see `pip.build-failed`).",
        ),
    ),
    Rule(
        id="pip.resolution-impossible",
        title="pip cannot satisfy conflicting version constraints",
        category=Category.DEPENDENCY,
        patterns=(
            r"ResolutionImpossible",
            r"because these package versions have conflicting dependencies",
            r"The conflict is caused by",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "Two requirements demand mutually exclusive versions of the same package. pip's "
            "backtracking resolver exhausted its options and reported the conflict rather than "
            "silently installing something broken."
        ),
        fixes=(
            "Read the 'The conflict is caused by' block - it lists each constraint and its source.",
            "Loosen or upgrade whichever pin is stale, then re-lock.",
            "Use `pip-compile` / `uv pip compile` so conflicts surface when locking, not in CI.",
        ),
    ),
    Rule(
        id="pip.build-failed",
        title="Building a package from source failed",
        category=Category.BUILD,
        patterns=(
            r"Failed building wheel for",
            r"error: command '\S*(?:gcc|cc|clang|g\+\+)' failed",
            r"Microsoft Visual C\+\+ \d+\.\d+ is required",
            r"error: subprocess-exited-with-error",
            r"fatal error: \S+\.h: No such file or directory",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "No prebuilt wheel matched this platform, so pip fell back to compiling from source "
            "and the compiler or its headers were missing. Slim and Alpine images hit this "
            "constantly because they ship no toolchain."
        ),
        fixes=(
            "Install a toolchain first: `apt-get install -y build-essential python3-dev` "
            "(Debian) or `apk add build-base python3-dev` (Alpine).",
            "Add the -dev package for whatever header the error names (e.g. `libpq-dev`, `libffi-dev`).",
            "Better: switch off Alpine - `python:3.12-slim` has manylinux wheels available and "
            "usually needs no compilation at all.",
        ),
    ),
    Rule(
        id="python.module-not-found",
        title="Python cannot import a module",
        category=Category.DEPENDENCY,
        patterns=(r"ModuleNotFoundError: No module named", r"ImportError: No module named"),
        confidence=Confidence.MEDIUM,
        explanation=(
            "An import failed at runtime. Either the dependency was never installed in this job, "
            "it is a dev-only dependency missing from the CI install, or the package is installed "
            "but the working directory / `PYTHONPATH` hides the local module."
        ),
        fixes=(
            "Add the missing package to requirements (or the `dev`/`test` extra CI installs).",
            "Install the project itself so its packages are importable: `pip install -e .`.",
            "If it is a first-party module, check the job's working directory and `PYTHONPATH`.",
            "Confirm the install step ran in the same job - installs do not carry across jobs "
            "without a cache or artifact.",
        ),
    ),
    Rule(
        id="poetry.lock-stale",
        title="poetry.lock is stale relative to pyproject.toml",
        category=Category.DEPENDENCY,
        patterns=(
            r"pyproject\.toml changed significantly since poetry\.lock was last generated",
            r"poetry lock \[--no-update\]",
        ),
        confidence=Confidence.HIGH,
        explanation="Poetry refuses to install from a lockfile that no longer matches the manifest.",
        fixes=(
            "Run `poetry lock --no-update` and commit `poetry.lock`.",
            "Use `poetry check --lock` in CI to catch drift before the install step.",
        ),
    ),
)

OTHER_ECOSYSTEM_RULES: tuple[Rule, ...] = (
    Rule(
        id="maven.resolve-failed",
        title="Maven could not resolve dependencies",
        category=Category.DEPENDENCY,
        patterns=(
            r"Could not resolve dependencies for project",
            r"Non-resolvable parent POM",
            r"Failed to read artifact descriptor for",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "Maven could not fetch an artifact - it is absent from the configured repositories, "
            "or the job cannot authenticate to a private one."
        ),
        fixes=(
            "Confirm the coordinates and version exist in a repository the build declares.",
            "For a private repo, provide `settings.xml` with a `<server>` entry using CI variables.",
            "A SNAPSHOT dependency may have been cleaned up - pin a release version.",
        ),
    ),
    Rule(
        id="gradle.daemon-lost",
        title="Gradle daemon disappeared (usually killed for memory)",
        category=Category.RESOURCES,
        patterns=(
            r"Gradle build daemon disappeared unexpectedly",
            r"Daemon will be stopped at the end of the build after running out of JVM memory",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The Gradle daemon died mid-build. In containers this is almost always the OOM "
            "killer, often because the JVM sized its heap from the host's memory rather than "
            "the container's limit."
        ),
        fixes=(
            'Constrain the JVM: `GRADLE_OPTS: "-Xmx2g"` and `org.gradle.jvmargs=-Xmx2g`.',
            "Disable the daemon in CI: `--no-daemon` (it buys nothing in a one-shot container).",
            "Raise the container's memory limit, or lower `--max-workers`.",
        ),
    ),
    Rule(
        id="go.missing-gosum",
        title="go.sum is missing entries",
        category=Category.DEPENDENCY,
        patterns=(
            r"missing go\.sum entry",
            r"updates to go\.mod needed",
            r"to add it:\s*\n?\s*go mod download",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "go.mod references a module that go.sum does not record. Go refuses to build rather "
            "than trust an unverified module."
        ),
        fixes=(
            "Run `go mod tidy` locally and commit both `go.mod` and `go.sum`.",
            "Add `go mod verify` to CI so drift fails fast and obviously.",
        ),
    ),
    Rule(
        id="bundler.frozen",
        title="Gemfile.lock is out of date (deployment mode)",
        category=Category.DEPENDENCY,
        patterns=(
            r"[Tt]he dependencies in your Gemfile changed",
            r"frozen mode",
            r"deployment mode.*Gemfile",
            r"You have added to the Gemfile",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "Bundler runs frozen/deployment in CI and found Gemfile and Gemfile.lock disagree."
        ),
        fixes=(
            "Run `bundle install` locally and commit the updated `Gemfile.lock`.",
            "Do not run `bundle install --no-deployment` in CI just to get past this.",
        ),
    ),
    Rule(
        id="composer.lock-stale",
        title="composer.lock is out of date",
        category=Category.DEPENDENCY,
        patterns=(
            r"Your lock file does not contain a compatible set of packages",
            r"The lock file is not up to date with the latest changes in composer\.json",
        ),
        confidence=Confidence.HIGH,
        explanation="composer.json and composer.lock disagree, or the lock targets a different PHP version.",
        fixes=(
            "Run `composer update --lock` and commit `composer.lock`.",
            "Match the job's PHP version to the one the lockfile was resolved against.",
        ),
    ),
    Rule(
        id="cargo.locked",
        title="Cargo.lock would need updating but --locked was passed",
        category=Category.DEPENDENCY,
        patterns=(
            r"the lock file \S+ needs to be updated but --locked was passed",
            r"the lock file .* needs to be updated",
        ),
        confidence=Confidence.HIGH,
        explanation="Cargo.toml changed without regenerating Cargo.lock.",
        fixes=("Run `cargo update --workspace` (or plain `cargo build`) and commit `Cargo.lock`.",),
    ),
)

TEST_BUILD_RULES: tuple[Rule, ...] = (
    Rule(
        id="test.pytest-failed",
        title="pytest reported failing tests",
        category=Category.TEST,
        patterns=(
            r"=+ (?:FAILURES|ERRORS) =+",
            r"=+ short test summary info =+",
            r"\b\d+ failed(?:,| \b)",
            r"^FAILED \S+::",
        ),
        excludes=(r"\b0 failed\b",),
        confidence=Confidence.HIGH,
        explanation=(
            "The job failed because tests failed, not because the environment is broken. The "
            "short test summary lists each failing node id."
        ),
        fixes=(
            "Reproduce locally: `pytest <nodeid>` from the summary line.",
            "If it passes locally, look for CI-only state: timezone, locale, ordering, network access.",
            "Publish a JUnit report (`--junitxml`) so GitLab shows failures on the MR page.",
        ),
    ),
    Rule(
        id="test.jest-failed",
        title="Jest reported failing tests",
        category=Category.TEST,
        patterns=(
            r"Tests:.*\d+ failed",
            r"Test Suites:.*\d+ failed",
            r"●\s+\S.*›",
        ),
        excludes=(r"Tests:\s+0 failed",),
        confidence=Confidence.HIGH,
        explanation="Jest exited non-zero because assertions failed in one or more suites.",
        fixes=(
            "Run the named suite locally: `npx jest path/to/file`.",
            "Snapshot mismatches usually mean an intentional change - update with `jest -u` and commit.",
            "For CI-only flakes, check for real timers, shared globals, or test ordering dependence.",
        ),
    ),
    Rule(
        id="test.junit-failed",
        title="JUnit/Surefire reported failing tests",
        category=Category.TEST,
        patterns=(
            r"Tests run: \d+, Failures: [1-9]",
            r"Tests run: \d+, Failures: \d+, Errors: [1-9]",
            r"There are test failures",
        ),
        confidence=Confidence.HIGH,
        explanation="The Maven/Gradle test task failed because assertions failed.",
        fixes=(
            "Open the surefire/failsafe report for the stack trace of each failure.",
            "Upload the reports as artifacts and wire `reports: junit:` so GitLab renders them.",
        ),
    ),
    Rule(
        id="test.go-failed",
        title="Go tests failed",
        category=Category.TEST,
        patterns=(r"^\s*--- FAIL: ", r"^FAIL\s+\S+"),
        confidence=Confidence.HIGH,
        explanation="`go test` reported one or more failing tests.",
        fixes=(
            "Re-run just the failing test: `go test -run '^TestName$' ./pkg/...`.",
            "If it only fails in CI, try `-race` and `-count=1` locally - caching and parallelism differ.",
        ),
    ),
    Rule(
        id="build.typescript",
        title="TypeScript compilation errors",
        category=Category.BUILD,
        patterns=(r"error TS\d+:",),
        confidence=Confidence.HIGH,
        explanation=(
            "`tsc` rejected the code. If it compiles locally, the usual cause is a difference in "
            "installed types - a lockfile not committed, or a `@types/*` version that floated."
        ),
        fixes=(
            "Run `npx tsc --noEmit` locally to reproduce.",
            "Make sure the lockfile is committed so CI resolves identical `@types/*` versions.",
            "Check `skipLibCheck` and `strict` are the same in CI as locally - one config, no overrides.",
        ),
    ),
    Rule(
        id="build.module-not-found",
        title="Bundler cannot resolve an import",
        category=Category.BUILD,
        patterns=(
            r"Module not found: Error: Can't resolve",
            r"Cannot find module '\S+'",
            r"Failed to resolve import",
        ),
        confidence=Confidence.MEDIUM,
        explanation=(
            "An import path did not resolve. Case-sensitivity is the classic CI-only cause: "
            "macOS filesystems are case-insensitive, Linux runners are not."
        ),
        fixes=(
            "Check the import's exact casing against the file on disk - `./Utils` vs `./utils`.",
            "Confirm the package is a real dependency, not just present in someone's local node_modules.",
            "For path aliases, ensure the bundler and `tsconfig.json` declare the same mapping.",
        ),
    ),
    Rule(
        id="lint.eslint",
        title="ESLint reported problems",
        category=Category.LINT,
        patterns=(r"✖ \d+ problems?", r"\d+ problems? \(\d+ errors?"),
        excludes=(r"✖ 0 problems", r"\(0 errors"),
        confidence=Confidence.HIGH,
        explanation="ESLint exited non-zero. With `--max-warnings 0`, warnings fail the job too.",
        fixes=(
            "Run `npx eslint . --fix` locally and commit the result.",
            "If warnings are failing the build, decide deliberately: fix them or raise `--max-warnings`.",
        ),
    ),
)

CONFIG_RULES: tuple[Rule, ...] = (
    Rule(
        id="ci.config-invalid",
        title=".gitlab-ci.yml is invalid",
        category=Category.CONFIG,
        patterns=(
            r"Found errors in your \.gitlab-ci\.yml",
            r"jobs config should contain",
            r"Invalid configuration format",
            r"config should be a hash",
            r"This GitLab CI configuration is invalid",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "GitLab rejected the pipeline definition before running anything. The message names "
            "the offending key."
        ),
        fixes=(
            "Validate in the UI: CI/CD → Editor → Validate, which resolves `include:` too.",
            "Or lint via API: `POST /projects/:id/ci/lint` with the file contents.",
            "Check `include:`d files as well - the error may originate in one of them.",
        ),
        docs=(_YAML_DOCS, _CI_DOCS),
    ),
    Rule(
        id="ci.artifact-missing",
        title="Artifact upload found no matching files",
        category=Category.CONFIG,
        patterns=(
            r"WARNING: \S+: no matching files",
            r"ERROR: No files to upload",
            r"no matching files\. Ensure that the artifact path is relative",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The `artifacts: paths:` glob matched nothing. Paths are relative to the project "
            "directory, so an absolute path or one pointing outside the build dir never matches."
        ),
        fixes=(
            "Make the path relative to `$CI_PROJECT_DIR` - not absolute, not `../`.",
            "`ls -la` the directory in the job to confirm the build actually produced the file.",
            "If the producing step failed silently, the upload is a symptom, not the cause.",
        ),
        docs=(_ARTIFACT_DOCS,),
    ),
    Rule(
        id="ci.artifact-download-failed",
        title="Could not download artifacts from an earlier job",
        category=Category.CONFIG,
        patterns=(
            r"ERROR: Downloading artifacts from coordinator.*(?:404|not found)",
            r"WARNING: Removing .* artifacts",
            r"ERROR: Download request redirected",
        ),
        confidence=Confidence.MEDIUM,
        explanation=(
            "This job expected artifacts from an upstream job that never produced them, or "
            "whose artifacts have already expired."
        ),
        fixes=(
            "Check the upstream job actually succeeded and uploaded artifacts.",
            "Name the producing jobs explicitly with `dependencies:` or `needs:`.",
            "Extend `artifacts: expire_in:` if the pipeline runs long enough for them to expire.",
        ),
        docs=(_ARTIFACT_DOCS,),
    ),
    Rule(
        id="ci.cache-failed",
        title="Cache could not be restored",
        category=Category.CONFIG,
        patterns=(
            r"Failed to extract cache",
            r"WARNING: Cache file does not exist",
            r"Failed to (?:create|download) cache",
        ),
        confidence=Confidence.LOW,
        explanation=(
            "The runner could not restore the cache. This is usually harmless on its own - the "
            "job just rebuilds from scratch - but it is a real failure if a later step assumes "
            "the cache exists."
        ),
        fixes=(
            "A missing cache on the first run of a new `cache: key` is expected.",
            "Never let a job depend on cache contents for correctness; caches are best-effort.",
            "For distributed caching, verify the runner's S3/GCS cache credentials and bucket.",
        ),
        docs=(_CACHE_DOCS,),
    ),
)

SHELL_RULES: tuple[Rule, ...] = (
    Rule(
        id="shell.command-not-found",
        title="A command in the script does not exist in the image",
        category=Category.SCRIPT,
        patterns=(
            # bash: "line 3: foo: command not found"
            r"command not found",
            # dash/ash (Alpine): "/bin/sh: eval: line 120: terraform: not found"
            r":\s*not found\s*$",
            # docker/containerd exec
            r"executable file not found in \$PATH",
            r"starting container process caused: exec: \S+: executable file not found",
        ),
        confidence=Confidence.HIGH,
        exit_codes=(127,),
        explanation=(
            "The shell could not find a binary the script calls. Exit code 127 is its signature. "
            "The image simply does not ship that tool, or an install step that would have "
            "provided it did not run."
        ),
        fixes=(
            "Install the tool in `before_script`, or pick an image that already includes it.",
            "For a locally installed CLI, add its directory to `PATH` "
            '(e.g. `export PATH="$PWD/node_modules/.bin:$PATH"`).',
            "Alpine images lack `bash`, `curl`, `git` and much else by default - `apk add` them "
            "or use a `-slim` Debian image.",
        ),
    ),
    Rule(
        id="shell.permission-denied",
        title="A file is not executable, or the job cannot write there",
        category=Category.SCRIPT,
        patterns=(r"[Pp]ermission denied", r"^\S+: cannot execute"),
        confidence=Confidence.MEDIUM,
        exit_codes=(126,),
        explanation=(
            "The command exists but could not be executed or the path could not be written. "
            "Git does not preserve the executable bit unless it was committed."
        ),
        fixes=(
            "Commit the executable bit: `git update-index --chmod=+x script.sh`.",
            "Or invoke through the interpreter: `bash script.sh` instead of `./script.sh`.",
            "If it is a write failure, check the container user - many hardened images run as non-root.",
        ),
    ),
    Rule(
        id="shell.no-such-file",
        title="A path the script expects does not exist",
        category=Category.SCRIPT,
        patterns=(r"No such file or directory",),
        confidence=Confidence.LOW,
        explanation=(
            "A referenced file or directory is absent. In CI this usually means the working "
            "directory differs from local, or an earlier step that should have created it failed."
        ),
        fixes=(
            "Print `pwd` and `ls -la` around the failing command to see the real layout.",
            "Each job starts from a fresh checkout - files created in another job need artifacts.",
            "Check relative paths against `$CI_PROJECT_DIR`.",
        ),
    ),
)

CLOUD_NETWORK_RULES: tuple[Rule, ...] = (
    Rule(
        id="aws.no-credentials",
        title="AWS credentials missing or invalid",
        category=Category.AUTH,
        patterns=(
            r"Unable to locate credentials",
            r"InvalidClientTokenId",
            r"ExpiredToken",
            r"The security token included in the request is (?:invalid|expired)",
            r"AccessDenied(?:Exception)?\b",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The AWS SDK/CLI found no usable credentials, or the ones it found are expired or "
            "lack the needed permission."
        ),
        fixes=(
            "Confirm `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (and `AWS_DEFAULT_REGION`) are set.",
            "Protected variables only exist on protected branches and tags - check the branch.",
            "Prefer OIDC: GitLab can mint short-lived AWS credentials via `id_tokens`, with no stored secret.",
            "For AccessDenied, the credential is valid but the IAM policy is missing an action.",
        ),
    ),
    Rule(
        id="kube.forbidden",
        title="Kubernetes rejected the request",
        category=Category.DEPLOY,
        patterns=(
            r"Error from server \(Forbidden\)",
            r"You must be logged in to the server \(Unauthorized\)",
            r"error: You must be logged in to the server",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The cluster authenticated the caller but denied the action (Forbidden), or could "
            "not authenticate at all (Unauthorized). Both point at the service account's RBAC "
            "or an expired kubeconfig token."
        ),
        fixes=(
            "Check the ServiceAccount's Role/ClusterRole covers the verb and resource in the message.",
            "Confirm the kubeconfig/token variable is present and not expired for this branch.",
            "Verify the namespace - a Role is namespace-scoped and does not apply elsewhere.",
        ),
    ),
    Rule(
        id="net.dns",
        title="DNS resolution failed",
        category=Category.NETWORK,
        patterns=(
            r"Temporary failure in (?:name resolution|resolving)",
            r"Could not resolve host",
            r"getaddrinfo (?:ENOTFOUND|EAI_AGAIN)",
            r"no such host",
            r"Name or service not known",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The job could not resolve a hostname. Either the name is wrong, the runner has no "
            "outbound DNS, or - when the name is a CI service - the service container never "
            "started so its alias does not exist."
        ),
        fixes=(
            "If the host is a `services:` alias, check that service started (its logs precede the script).",
            "Wait for services to be ready before using them; container start is not readiness.",
            "On a restricted network, confirm the runner's DNS and any proxy settings.",
        ),
    ),
    Rule(
        id="net.tls",
        title="TLS certificate verification failed",
        category=Category.NETWORK,
        patterns=(
            r"x509: certificate signed by unknown authority",
            r"SSL certificate problem",
            r"CERTIFICATE_VERIFY_FAILED",
            r"unable to get local issuer certificate",
            r"self.signed certificate",
        ),
        confidence=Confidence.HIGH,
        explanation=(
            "The job could not verify the server's certificate. Behind a corporate TLS-inspecting "
            "proxy, or against an internal service with a private CA, the runner's trust store "
            "does not contain the issuing CA."
        ),
        fixes=(
            "Install the internal CA into the job image's trust store "
            "(`update-ca-certificates` after dropping the PEM into `/usr/local/share/ca-certificates/`).",
            "For gitlab-runner itself, mount the CA and set `tls-ca-file` in its config.",
            "Do not reach for `--insecure` / `GIT_SSL_NO_VERIFY` - that disables the check rather than fixing it.",
        ),
    ),
    Rule(
        id="net.connection-refused",
        title="Connection refused or reset",
        category=Category.NETWORK,
        patterns=(
            r"[Cc]onnection refused",
            r"\bECONNREFUSED\b",
            r"[Cc]onnection reset by peer",
            r"failed to connect to \S+ port",
        ),
        confidence=Confidence.MEDIUM,
        explanation=(
            "Nothing was listening at the address, or the connection was dropped. When the "
            "target is a CI service (database, redis) it usually means the script started before "
            "the service finished booting."
        ),
        fixes=(
            "Poll for readiness before using a service, rather than assuming it is up.",
            "Reach services by their alias hostname (`postgres`, `redis`), not `localhost`, "
            "unless the runner uses the shell executor.",
            "Confirm the port matches what the service actually exposes.",
        ),
    ),
    Rule(
        id="gitlab.token-denied",
        title="GitLab rejected the token",
        category=Category.AUTH,
        patterns=(
            r"HTTP Basic: Access denied",
            r"401 Unauthorized",
            r"403 Forbidden",
            r"insufficient_scope",
        ),
        confidence=Confidence.MEDIUM,
        explanation=(
            "A request to GitLab was refused. The token is absent, expired, lacks the required "
            "scope, or is a protected variable being used on an unprotected branch."
        ),
        fixes=(
            "Check the token's expiry - GitLab tokens expire by default now.",
            "Match scopes to the operation (`api`, `read_repository`, `write_registry`, …).",
            "Protected variables are only injected on protected branches/tags.",
            "For cross-project access with `CI_JOB_TOKEN`, add this project to the target's "
            "job token allowlist.",
        ),
    ),
)

ALL_RULES: tuple[Rule, ...] = (
    *RUNNER_RULES,
    *DOCKER_RULES,
    *GIT_RULES,
    *NODE_RULES,
    *PYTHON_RULES,
    *OTHER_ECOSYSTEM_RULES,
    *TEST_BUILD_RULES,
    *CONFIG_RULES,
    *SHELL_RULES,
    *CLOUD_NETWORK_RULES,
)


def rules_by_category() -> dict[Category, list[Rule]]:
    grouped: dict[Category, list[Rule]] = {}
    for rule in ALL_RULES:
        grouped.setdefault(rule.category, []).append(rule)
    return grouped


def get_rule(rule_id: str) -> Rule | None:
    for rule in ALL_RULES:
        if rule.id == rule_id:
            return rule
    return None
