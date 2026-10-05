# The pipelinemd image: the CLI with the Claude diagnosis layer, ready to name
# in a CI job's `image:` so a failed pipeline is diagnosed without a pip install
# - which is exactly when PyPI being slow or unreachable would hurt most.
#
# Two stages. The builder turns the source tree into wheels for pipelinemd and
# its [llm] dependencies; the runtime stage installs from those wheels alone,
# so the published image carries no build backend, no source tree and no pip
# cache.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src

# Only what the build backend reads. Copying the whole context would make every
# edit to a test or a doc invalidate this layer.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip wheel --wheel-dir /wheels ".[llm]"


FROM python:${PYTHON_VERSION}-slim

# Set from the release tag by the release workflow and from __version__ by
# `make image`; a bare `docker build` says "dev".
ARG VERSION=dev

LABEL org.opencontainers.image.title="pipelinemd" \
      org.opencontainers.image.description="GitLab CI/CD failure doctor - diagnoses failed pipelines and suggests fixes." \
      org.opencontainers.image.source="https://github.com/rbalukja15/pipelinemd" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# The wheels are bind-mounted rather than copied, so they never become a layer
# of their own. --no-index keeps pip off the network entirely: what gets
# installed is exactly what the builder resolved.
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    pip install --no-index --find-links /wheels --root-user-action=ignore "pipelinemd[llm]"

# A fixed numeric uid, not just a name: Kubernetes runners with runAsNonRoot
# can only verify a number. GitLab's Docker executor makes the build directory
# writable for whichever user the image declares.
RUN useradd --create-home --uid 10001 --user-group pipelinemd
USER 10001:10001
WORKDIR /home/pipelinemd

# GitLab runs job scripts through a shell, so a job using this image overrides
# the entrypoint with `entrypoint: [""]`; the slim base keeps /bin/sh for that.
# Everywhere else the image is the CLI itself: `docker run <image> distill -`.
ENTRYPOINT ["pipelinemd"]
CMD ["--help"]

# `docker stop` (and compose and Kubernetes) send the image's stop signal.
# Python running as PID 1 ignores SIGTERM but turns SIGINT into
# KeyboardInterrupt, which the CLI already answers with "interrupted" and exit
# 130 - so a stop takes a fraction of a second instead of waiting 10 s for
# SIGKILL.
STOPSIGNAL SIGINT
