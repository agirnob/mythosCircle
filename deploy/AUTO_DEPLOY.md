# Branches and automatic releases

New work starts on `feature/<name>` or `fix/<name>` from `develop`. Open a pull
request into `develop`, then release with a `develop` → `main` pull request.
The rules for future agents are in `AGENTS.md`.

`.github/workflows/pipeline.yml` runs backend lint/tests and frontend
lint/type checks/tests/build on work branches and pull requests. The `CI gate`
must pass before a release. It uses locked project dependencies and pinned
GitHub Actions. Tests use temporary databases and mocked generation providers;
they do not require the image, video, or LLM servers.
Checks rerun when a pull request is edited, including a change of base branch,
so a gate failure for an old target is replaced by a check against the new target.

Only `main` pushes (or a manual workflow run on `main`) build release Docker
images and deploy. GitHub builds both images, labels them with the commit SHA,
and transfers them as a short-lived Actions artifact. The production runner
loads those exact images and checks their labels against the checked-out SHA.
PR jobs never use the production runner. Deployment is serialized and is not
cancelled when another release arrives.

## One-time GitHub setup

1. In **Settings → Actions → General**, allow this repository's Actions.
2. In **Settings → Actions → Runners → New self-hosted runner**, choose Linux
   x64. Register the installed runner in `/home/homest/actions-runner` with the
   displayed temporary token, name `mythoscircle-production`, labels
   `mythoscircle-production`, and work directory `_work`. The SSH deploy key
   authenticates Git reads; it does not register an Actions runner.
3. Install `deploy/mythoscircle-actions-runner.service` as
   `~/.config/systemd/user/mythoscircle-actions-runner.service` on the server,
   then run `systemctl --user daemon-reload` and
   `systemctl --user enable --now mythoscircle-actions-runner.service`.
   User lingering must be enabled so logout does not stop the runner.
4. Create the `production` environment under **Settings → Environments** and
   restrict deployment branches to `main`. Leave required reviewers off if
   releases should be automatic after merging.
5. Where available for this private repository's GitHub plan, protect `main`
   and `develop`: require pull requests, require the `CI gate` check, require
   branches to be current before merging, and disallow force pushes/deletion.
   Solo work does not need an extra reviewer. Protection must be configured in
   GitHub; the instructions in `AGENTS.md` do not enforce it server-side.

No registry password, SSH private key, or GitHub personal token is needed in
workflow secrets. The runner uses GitHub's per-job token to check out the
private repository. Restrict this runner to this repository and keep deployment
jobs on trusted `main` releases.

## Production behavior

The existing Compose stack `/home/homest/mythos-redeploy.yml`, runtime settings,
and data volume remain managed through Portainer. Generation jobs are given up
to ten minutes to finish; if still busy, deployment fails visibly and can be
rerun. A job arriving just before the switch also aborts the switch. Web access
is briefly stopped for the database/media snapshot and release change.

Snapshots live at `/data/backups/autodeploy/` (14 retained). The API must pass
its health check before web starts. A failed switch restores previous images
and, when the API was changed, the verified data snapshot. The Actions job
remains failed after rollback. A failed revision is not retried without an
operator clearing `~/.local/state/mythoscircle-deploy/failed-revision` first.
A busy deployment does not record a failed revision.

The deployed SHA is in `~/.local/state/mythoscircle-deploy/deployed-revision`.
Actions logs show builds and deployment results. Runner logs are available with:

```sh
systemctl --user status mythoscircle-actions-runner.service
journalctl --user -u mythoscircle-actions-runner.service -n 100
```

Pause future deployment jobs by stopping the runner while idle:

```sh
systemctl --user disable --now mythoscircle-actions-runner.service
```

The running application keeps serving while the runner is stopped. To resume,
enable/start the service again. Do not interrupt a deployment during backup or
rollback. For an emergency manual release of a checked-out `main` revision,
`bash deploy/auto-deploy.sh` can also build images locally on the server.


## Administrator access

Administrator grants are environment-only account ULIDs in
`MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS` (comma-separated, whitespace trimmed,
duplicates ignored). Empty grants nobody; malformed nonempty IDs prevent
startup. Registration stays open and never grants administrator access.
Suspension applies to one account, leaving its campaigns and queued work intact.
Old signed portrait links remain independently valid until expiry (up to seven days).

To activate access, sign in to an existing **active** account and obtain its
`id` from authenticated `GET /api/auth/me` (browser developer tools or an
authenticated API client). In the actual Portainer-managed API service in
`/home/homest/mythos-redeploy.yml`, wire `MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS` into
the API container environment and supply that account ID. Recreate the API
container, then verify `/api/auth/me` returns `is_admin: true`. Changes to
repository Compose templates alone cannot activate the external stack. A
Portainer variable without a matching service environment entry also has no
effect. Keep the external Compose configuration and deployment runner's
interpolation environment consistent: the runner does not automatically inherit
Portainer variables. A literal environment entry in the externally managed
service is another operator-managed option. Never put grants in frontend build
variables or `config.toml`.

To revoke access, remove the ID and recreate the API container. Detection occurs
on the next admin navigation/request. To recover from a syntactically valid
wrong ID or an allowlist containing only disabled accounts, use operator access
to replace it with a known active account's ID and recreate the API. Startup
never promotes accounts or edits suspension state. Another active administrator
may restore a disabled account that was subsequently added to the allowlist;
configured administrators cannot be disabled through the application.

These are operator activation/recovery instructions. Feature implementation does
not change production configuration, deploy a development branch, or bypass the
`CI gate` and `develop` → `main` release pull request.
