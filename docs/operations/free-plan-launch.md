# Hosted Free plan launch

The registration switch stays closed during the application deploy. This avoids
accepting a user before the migration, legal pages and health checks are all live.

## Release order

1. Obtain legal review of the hosted Terms and Privacy Notice. Confirm the operator
   identity, contact address, subprocessors, retention and deletion procedure.
2. Merge with `PUBLIC_SIGNUP=false` and deploy the application. The workflow runs
   `alembic upgrade head` with the new image before replacing the web container.
3. Deploy the public site so every `Start free` link and notice version is live.
4. Verify while closed:
   - `/health/ready` returns 200;
   - `/login` offers every configured OAuth provider;
   - `/signup` redirects to `/login`;
   - migration head is `v0021`;
   - existing password, OAuth and MCP access still works.
5. Run the `VM env sync` workflow with `public_signup=true`. It recreates only the
   app container and confirms `/login` returns 200.
6. Verify from a new private browser session:
   - `/signup` shows Free limits and requires consent;
   - GitHub and Google callbacks use the registered app URL;
   - the first callback creates a Free account and reaches `/welcome`;
   - onboarding renames the starter project and reveals one MCP token once;
   - a second project and fourth active token are rejected;
   - returning OAuth login reaches the existing account, not another tenant.

## Observe

Search application logs for `oauth_authenticated`, `free_onboarding_completed` and
`oauth_rate_limited`. These events intentionally contain no email, provider subject,
IP address, token or project content.

## Close registration without rollback

Run `VM env sync` with `public_signup=false`. Existing users continue to sign in;
only new account provisioning stops. Do this first if abuse, provider failure or an
unexpected onboarding error appears.

Do not downgrade `v0021` after real OAuth users exist: the older `v0020` downgrade
already refuses databases containing passwordless users. Restore the pre-release
database backup only for a full incident rollback.
