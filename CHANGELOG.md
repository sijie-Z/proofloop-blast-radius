# Changelog

## v0.1.4 — 2026-11-08

- In-UI evidence hash verification and verification replay.
- CI asserts `HASH OK`, `consistent: true`, and the replay verdict.
- Refreshed UI screenshot, slide 7 and the video script.

## v0.1.3 — 2026-11-08

- CI now starts the web app, runs the containment probe, and runs the keyless demo through the web API.
- The containment probe is UID-safe on POSIX: it falls back when the host UID has no passwd entry in the sandbox image.
- Asserts `network == blocked`, `secret_env == []`, scene 1 PASS and scene 2 FAIL in CI.

## v0.1.2 — 2026-11-08

- Web control plane (FastAPI + single-page UI), containment probe, sanitized evidence endpoints.
- Vultr VM deployment assets: bootstrap, systemd, Caddy, optional gVisor runtime.
- 10-slide deck, video script with the required containment moment, English subtitles, cover image.
- CI: unit tests on Python 3.11/3.12 plus the keyless Docker demo self-test; POSIX sandbox runs as the host UID.

## v0.1.1 — 2026-11-08

- Unit tests and the CI quality gate.
- Git Flow and contribution documentation.

## v0.1.0 — 2026-11-08

- Initial public demo: Vultr client, ReAct sandbox agent, ProofLoop verification and evidence.
