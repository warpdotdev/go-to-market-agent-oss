# Security Policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report privately using GitHub's **"Report a vulnerability"** feature
(the repository's **Security → Advisories** tab). If that is unavailable,
contact the repository owner directly to arrange private disclosure.

When reporting, please include:

- A description of the issue and its potential impact.
- Steps to reproduce (proof-of-concept if possible).
- Affected files, versions, or configurations.

We will acknowledge receipt, investigate, and coordinate a fix and disclosure
timeline with you.

## Scope and secrets

This repository is intended to contain **no real secrets, credentials, or
customer data**. All configuration is supplied at runtime via environment
variables (see `CONTRIBUTING.md`). Example/placeholder values in the code
(e.g. `example-gcp-project`, `example-artifacts-bucket`, `000000000`) are not
real.

If you believe a real secret or personal/customer data has been committed,
treat it as a security issue and report it privately so it can be rotated and
purged from history.
