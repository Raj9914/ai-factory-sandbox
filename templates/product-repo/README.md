# &lt;Product&gt;

> Ship48 product repository. Created from the factory `templates/product-repo` template (version in `TEMPLATE_VERSION`).

Replace `<Product>` and the placeholder text below when the repository is provisioned. Keep this repository **private**: it holds the PRD, research, experiment logs and code for one product.

## Where everything lives
| Path | Contents |
|---|---|
| `docs/PRD.md` | Product requirements (start from the template already in the file) |
| `docs/research/` | Market, competitor and user research |
| `docs/templates/` | Journey, analytics, task, cost-log and hour-24/48 templates |
| `experiments/<id>/` | One folder per 48-hour experiment (see `experiments/README.md`) |
| `Makefile` | Stack-agnostic command contract used by CI, agents and humans |
| `dist/` | Build output (`make build`); not committed |
| `.github/` | CI, issue forms and PR template |
| `.ai-factory.yaml` | Verification commands the factory runs before review |
| `AGENTS.md`, `CLAUDE.md` | Agent rules |

## Command contract
```bash
make install              # install dependencies from the lockfile
make test                 # run all tests; CI runs this
make build                # build into dist/
make smoke URL=<base-url> # check a deployed preview or beta URL responds
```
Every stack (web, mobile, game, API) implements these four targets. Nothing else may be assumed by CI or agents.

## Rules
- No secrets, tokens or personal data in the repository, issues or PRs.
- Tasks start as `status:proposed`; only a human applies `status:ready`, merges PRs and approves production.
- One task = one owner = one branch = one PR.
