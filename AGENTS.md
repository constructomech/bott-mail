# AGENTS.md

## Engineering Direction

- This system is not in production yet. Do not keep compatibility baggage for older internal designs unless the user explicitly asks for it.
- Remove dead routes, unused config, stale helpers, and obsolete tests when replacing a design.
- Prefer the smallest complete design over temporary dual paths.
- If a change would preserve old behavior only for hypothetical compatibility, ask first or delete it.

## Documentation

- Maintain Mermaid sequence diagrams for mail automation flows as designs change.
- Keep diagrams useful for threat modeling by showing trust boundaries, credentials/scopes, callback validation, and approval points.

## Releases

- Do not manually push Docker images to GHCR from a workstation.
- To release, tag a version in git; the repository workflow builds and publishes the image from the tag.
