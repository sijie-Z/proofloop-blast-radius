# Contributing

## Branch model (Git Flow)

| Branch | Purpose |
| --- | --- |
| `main` | Production-ready releases. Only `release/*` and `hotfix/*` merge here. |
| `develop` | Integration branch. All feature work lands here first. |
| `feature/<slug>` | One issue, one branch, one pull request. Branched from `develop`. |
| `release/<version>` | Stabilization. Branched from `develop`, merged to `main`, tagged. |
| `hotfix/<version>` | Urgent fixes. Branched from `main`, merged to `main` and back to `develop`. |

Rules:

- Open an issue first. **One issue = one pull request.**
- Branch from `develop`; never commit directly to `main` or `develop`.
- Feature PRs target `develop`. Release and hotfix PRs target `main`.
- The PR body must contain `Closes #<issue>`.
- CI must be green before merge. `.github/workflows/ci.yml` runs:
  - unit tests on Python 3.11 and 3.12;
  - a keyless end-to-end demo self-test in Docker (`python demo.py --agent-mode fixed`).
- Use conventional commit messages: `feat:`, `fix:`, `test:`, `ci:`, `docs:`, `chore:`.
- Never commit secrets. `.env` is gitignored; the Vultr key is never required by CI.

## Local checks

```bash
python -m pytest -q
python demo.py --agent-mode fixed
```

The second command needs Docker and the sandbox image:

```bash
docker build -t proofloop-sandbox:latest docker
```

## Release process

1. Merge feature PRs into `develop` (one issue per PR, CI green).
2. Create `release/x.y.z` from `develop`.
3. Open a PR from `release/x.y.z` to `main`; CI must pass.
4. Merge, then tag `vX.Y.Z` on `main`.
5. Merge `main` back into `develop`.

## Pull request checklist

- [ ] Linked issue (`Closes #N`)
- [ ] Tests added or updated
- [ ] `python -m pytest -q` passes
- [ ] Keyless demo self-test passes (or the PR explains why it does not apply)
- [ ] No secrets, local absolute paths, `runs/` or `.scratch/` artifacts
