# GitHub Pages Development Site

The static development dashboard lives under `site/`.

## Files

- `site/index.html` — page structure
- `site/styles.css` — visual design
- `site/app.js` — renders repository project data
- `site/data/project.json` — status, roadmap, stack and changelog data
- `.github/workflows/pages.yml` — GitHub Pages deployment workflow

## Updating the site

When roadmap/status changes, update:

1. canonical Markdown docs (`docs/ROADMAP.md`, `CHANGELOG.md`, relevant specs);
2. `site/data/project.json` to keep the visual dashboard synchronized.

`AGENTS.md` instructs Codex to do this for relevant changes.

## Enabling deployment

The workflow uses GitHub's official Pages Actions flow.

In repository settings, Pages must use **GitHub Actions** as its publishing source. After that, changes to `site/**` on `main` trigger deployment.

## Private repository note

GitHub's current Pages policy allows Pages from public repositories on GitHub Free. Publishing Pages directly from a private repository requires a plan that supports private-repository Pages (for personal accounts, GitHub Pro or another eligible paid plan).

Also note that a standard GitHub Pages site is publicly accessible even when its source repository is private.

If this repository stays private on GitHub Free, keep the `site/` source here and later choose one of these options:

- make the repository public;
- upgrade to an eligible GitHub plan;
- mirror only the privacy-safe `site/` directory to a separate public Pages repository;
- deploy the same static `site/` directory to another free static host.

Never place secrets, private logs, screenshots, training samples or other personal data in `site/` because the deployed site is intended to be public.
