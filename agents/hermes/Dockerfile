# Work agent image: Hermes installed as the non-root ubuntu user (uid 1000).
# The official nousresearch/hermes-agent image starts s6-overlay as root, which OpenShell rejects.
# Build on the VM: docker build -t steward-hermes:local agents/hermes
FROM ubuntu:24.04

# git and curl are required by the installer. libatomic1 is installed here so the installer
# never needs sudo for it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git xz-utils libatomic1 \
    && rm -rf /var/lib/apt/lists/*

USER ubuntu
WORKDIR /home/ubuntu
ENV HOME=/home/ubuntu
ENV PATH=/home/ubuntu/.local/bin:$PATH

# Skip the browser and computer-use tools and the setup wizard.
RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh \
    | bash -s -- --skip-browser --skip-computer-use --non-interactive

COPY --chown=ubuntu:ubuntu config.yaml /home/ubuntu/.hermes/config.yaml

# Fail the build if the launcher is missing, and print the paths the work profile's
# binaries globs must cover.
RUN hermes --version \
    && launcher="$(readlink -f "$(command -v hermes)")" \
    && echo "hermes launcher: $launcher" \
    && echo "launcher first line: $(head -n 1 "$launcher")"

CMD ["hermes", "gateway", "run"]
