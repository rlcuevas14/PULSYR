# Dependency and supply-chain policy

Dependabot checks Python, npm, GitHub Actions and Docker weekly, with grouped minor
and patch updates where safe. Every PR runs secret scanning, locked backend tests,
`npm audit`, `pip-audit`, a HIGH/CRITICAL container scan and CycloneDX SBOM generation.
The image is built for inspection only; P5 does not push, sign, attest or deploy it.

| Severity | Target remediation | Exception authority |
|---|---:|---|
| Critical, known exploitable | 24 hours | Repository owner; max 7 days |
| High | 7 days | Repository owner; max 30 days |
| Medium | 30 days | Maintainer; max 90 days |
| Low | Next routine update | Maintainer |

An exception must name the advisory/CVE, affected component and version, exposure,
compensating controls, owner, approval date and expiry. Expired exceptions fail the
policy review. `ignore-unfixed` may reduce unactionable base-image noise, but a fixed
HIGH/CRITICAL finding blocks CI.

Release signing and registry attestations require an authorized image-publish
workflow with `id-token: write` and immutable digests. When that pipeline exists, it
must attach the generated SBOM and provenance to the promoted digest; this no-deploy
phase deliberately does not create a path that can publish from pull requests.
