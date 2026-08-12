# Public web measurement plan

Pulsyr selects one analytics system: Plausible, configured without advertising
cookies. It is rendered only when both `PUBLIC_ANALYTICS_ENABLED=true` and
`PUBLIC_DEPLOYMENT_ENVIRONMENT=production`. Staging and the private application are
excluded by construction. Search Console and Bing verification tokens are optional
build-time values; submitting ownership and the sitemap requires the domain operator.

| Event | Trigger | Purpose | Owner | Retention |
|---|---|---|---|---|
| `cta_app` | Public “Open the app” link | Product-to-app intent | Product owner | 13 months |
| `cta_github` | Repository/issues link | Open-source engagement | Product owner | 13 months |
| `cta_docs` | Quick-start link | Documentation intent | Product owner | 13 months |
| `quick_start_complete` | Full MCP guide after final quick-start step | Setup progression proxy | Product owner | 13 months |
| `contact` | Public contact channel | Support/security/legal intent | Service owner | 13 months |

No event includes account, user, project, item, search term, URL query, email address
or backlog content. Event names are fixed in versioned markup. A build contract checks
that production HTML loads the Plausible snippet exactly once and that disabled or
staging builds load it zero times.

For webmaster tools, set `PUBLIC_GOOGLE_SITE_VERIFICATION` and/or
`PUBLIC_BING_SITE_VERIFICATION` in the production public-site build, verify ownership,
then submit `https://pulsyr.dev/sitemap.xml`. These external account actions were not
performed during the no-deploy implementation phase.
