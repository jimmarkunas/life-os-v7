# GitHub Widgets

Small, static widgets hosted with GitHub Pages for embedding in Notion and other dashboards.

> **Canonical source: LIFE OS V7.** This `widgets/` directory in `life-os-v7` owns the canonical source code for
> LIFE OS widgets. Make all future widget development here, not independently in `github-widgets`.
>
> **Hosting during the transition:** the existing `github-widgets` repository remains the GitHub Pages hosting
> destination, and the existing Notion embeds that point at it must keep working. This copy is not yet connected
> to any automated deployment process: nothing here is built, served or scheduled by V7, so the Pages site stays
> the only live instance until a separately approved deployment path replaces the manual update.

## Widgets

- **World Clock** — live clocks and current weather for Honolulu, Los Angeles, Denver, Chicago, New York, London, and Paris.
- **Recruiting & Hiring** (`recruiting-hiring/`, LIFE-47, UI-only) — approved V2 interface on **synthetic data only**. It is not deployed: the Pages host is public, so no real recruiting data may ever be served from it, and an authenticated Notion embed is unproven (see LIFE-47).

Once GitHub Pages is enabled from the `main` branch root, the world clock will be available at:

`https://<github-user>.github.io/github-widgets/world-clock/`

## Structure

```text
github-widgets/
├── index.html
├── .nojekyll
└── world-clock/
    └── index.html
```
