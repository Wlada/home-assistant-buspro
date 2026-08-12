# Repository guide

This repository is the canonical source for the BusPro integration for Home
Assistant.

## Repository layout

- The repository root is the canonical development checkout.
- Integration files live in the repository root and are published through
  HACS with `content_in_root: true`.
- The deployed Home Assistant component lives at
  `/config/custom_components/buspro`.
- The canonical GitHub repository is `Wlada/home-assistant-buspro` on `main`.

## Development and deployment

Make source changes, run tests, and perform all Git operations in the canonical
checkout. Do not use a live Home Assistant configuration directory as the
primary Git worktree.

When this checkout is nested inside a larger Home Assistant configuration
repository, treat it as a separate Git repository. Do not stage BusPro source
through the parent repository, and do not put unrelated automations or
notification configuration in this repository.

Deploy the integration files to `/config/custom_components/buspro`. Do not
deploy repository metadata or local-only directories such as `.git`, `.idea`,
`.worktrees`, or `__pycache__`.

After deployment, validate the Home Assistant configuration and restart Home
Assistant so Python changes are loaded. The deployable integration content on
`main` is expected to match the live Home Assistant component. Ignore
line-ending-only differences when comparing the Windows checkout with the Home
Assistant filesystem.
