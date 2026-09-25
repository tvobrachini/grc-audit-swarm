# Security Policy

GRC Audit Swarm is a personal open-source project maintained by one person in their own time. There is no support contract or response-time commitment, but reports are welcome and will be read.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's private vulnerability reporting: the **Report a vulnerability** button in this repository's **Security** tab. Do not open a public issue for a security problem.

A useful report includes the affected file or endpoint, steps to reproduce, and the impact you expect.

## Supported versions

Only the current `master` branch is supported. There are no maintained release branches.

## Scope

In scope: the code in this repository, including the FastAPI backend (`src/api/`), the React frontend and its nginx configuration (`frontend/`), the evidence vault (`src/swarm/evidence.py`), the AWS evidence tools (`src/swarm/tools/aws_tools.py`), the Dockerfiles and `docker-compose.yml`.

Out of scope: vulnerabilities in third-party dependencies that are already tracked upstream (Dependabot and `pip-audit` run in CI), and deployments that expose the API or frontend beyond `127.0.0.1` without further protection. The project is built for local use behind a single shared token.

## Handling data safely

- The tool collects read-only evidence from AWS accounts you configure. Use a dedicated role with the read-only policy in the README.
- Never commit credentials, `.env` files or evidence files. `.env`, the session file (`data/audit_sessions.json`) and any `evidence_vault/` directory are git-ignored, and pre-commit runs detect-secrets.
- Model output and uploaded scope documents are untrusted input. Review every artifact at the human gates before relying on it.
