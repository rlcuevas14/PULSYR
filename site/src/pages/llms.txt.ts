import type { APIRoute } from "astro";

const body = `# Pulsyr

> Pulsyr is an open-source, self-hosted backlog that MCP-compatible coding agents can read and maintain while they work.

## Product
- [Product overview](https://pulsyr.dev/producto/)
- [MCP integration](https://pulsyr.dev/integraciones/mcp/)
- [Security model](https://pulsyr.dev/seguridad/)
- [Open source and self-hosting](https://pulsyr.dev/open-source/)

## Documentation
- [Get started](https://pulsyr.dev/docs/primeros-pasos/)
- [Source repository](https://github.com/rlcuevas14/PULSYR)
- [MCP reference](https://github.com/rlcuevas14/PULSYR/blob/main/docs/MCP.md)

This file is a concise navigation aid. The linked pages and repository are authoritative.
`;

export const GET: APIRoute = () => new Response(body, {
  headers: { "Content-Type": "text/plain; charset=utf-8" },
});
