# Security and data handling

The candidate runs a loopback web server and stores workplace data and memory locally. Requests to a configured hosted model send the supplied model context to that provider; choose the provider and accessible workspace with that in mind.

Models & Context stores credential references, not keys pasted into profile fields. Environment variables and supported local sealed stores supply credentials. Redaction and export checks reduce accidental disclosure but are not a promise that arbitrary model output or user-authored files can never contain sensitive information. Keep keys out of commissions, project files, source control and acceptance evidence.

Atelier enforces its configured call budget, consent, secret and harm boundaries and its file-tool permissions. This is not an operating-system sandbox for hostile code. Run only trusted candidate code and review which folders and tools a mind can access.

Packaging selects explicit files with byte commitments and rejects recognized secret material. It does not silently strip or rewrite private content. Scan findings and artifact membership are reviewed before public release. Resident homes, private memory, local profiles, credential stores and private Git history are excluded from this candidate.

Stop places before backup and use the shipped preservation tools; see PUBLIC_SETUP.md. Preserve originals during recovery. Report suspected vulnerabilities privately to the maintainers through a verified project contact or private advisory; do not put secrets in public issues.
