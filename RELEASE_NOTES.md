# Aether v0.2.0 · prototype

Prepared November 8, 2026. Windows application and matching public-source export.

## Downloads

| File | Contents |
|---|---|
| Aether-Windows-0.2.0.zip | Application, installer and bundled Python runtime |
| Aether-Source-0.2.0.zip | Public source, tests, documentation and selected historical evidence |
| SHA256SUMS.txt | Hashes of the release assets |

## In this release

- Chat with app Meridian supports agent-owned memory writes and retrieval across
  conversations. End conversation records the agent's exact closing words.
- The bounded Chat loop retains six requests with tools and a seventh without tools.
- The memory viewer shows when each note was first Recorded, with chronological
  sorting and a precise timestamp. Current until is explained as faster fading.
- API Keys explains assigned-key use and directs users without management access
  to their Admin.
- The workspace includes native Tasker preparation and execution, staff and teaching
  material, missions, roadmaps, records and the regenerated product roadmap seed.
- VERSION is the release-number source. Build metadata and Help → About report 0.2.0.

## Updating

Keep a backup of your existing data. The installer updates application files while
preserving the selected profile; use its existing login. Follow INSTALL_WITH_AI.md
or [PUBLIC_SETUP.md](PUBLIC_SETUP.md). Uninstalling the application keeps user data.

## Evidence and limits

[VALIDATION.md](VALIDATION.md) separates the package checks, prior live DeepSeek
results and remaining provider/clean-machine limits. The November 8 memory rehearsal
returned the original note ID in a new Chat and closed both sessions. It does not
measure general performance improvements from memory or guarantee model behavior.

This is a prototype, not a production-service certification. No hosted model usage
is included in the download. MIT license and third-party runtime notices are included.
