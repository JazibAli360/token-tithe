# Self-contained runtime: the host contributes only secrets (S5).
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends git make curl ca-certificates gnupg \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m tithe
WORKDIR /app
COPY --chown=tithe:tithe . /app
USER tithe
RUN make setup

ENTRYPOINT ["/app/.venv/bin/donate"]
CMD ["doctor"]
