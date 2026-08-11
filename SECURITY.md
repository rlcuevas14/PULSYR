# Security Policy

Pulsyr is a self-hosted application that handles authentication, API tokens,
and inbound webhooks: security reports are taken seriously.

## Supported versions

Only the latest tagged release receives security fixes. Upgrade to the newest
`v*` tag before reporting.

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Use GitHub's private vulnerability reporting: go to the repository's
**Security** tab → **Report a vulnerability**. This opens a private advisory
visible only to the maintainer.

Please include:

- Affected area (auth, MCP tokens, webhooks, project/account isolation, UI)
- Steps to reproduce or a proof of concept
- Impact assessment (what an attacker gains)

## What to expect

This is a solo-maintained project. You should receive an acknowledgment within
a few days. Confirmed vulnerabilities are fixed in the next release, and the
advisory is published once a fix is available.
