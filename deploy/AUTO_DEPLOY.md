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

## Primary and backup text generation providers

In **Administration → Text generation providers**, choose **Primary**, optionally
choose a distinct **Backup**, test each connection, then **Save provider settings**.
Local settings are prefilled from the deployment. Gemini is prefilled with
`gemini-3.8-flash` and Google's fixed HTTPS OpenAI-compatible endpoint. Advanced
provider settings also include OpenRouter at `https://openrouter.ai/api/v1/`,
prefilled with this ordered route:

1. `nvidia/nemotron-3.5-super-vl:free` (free, JSON Schema, 131,072 output tokens)
2. `google/gemma-4-26b-a4b-it:free` (free, JSON output, 32,768 output tokens)
3. `deepseek/deepseek-v4-flash-0731:nitro` (paid, JSON Schema, fast-provider routing)

OpenRouter tries the next model for rate limits and upstream errors. Gemma is
omitted for calls requesting over 32,768 output tokens. Provider selection sorts
by throughput and enforces a ceiling of $0.15/M input and $0.30/M output tokens;
if no provider meets the ceiling, the call fails without an unbounded charge.
The admin may edit the model and fallback list or restore this route with **Use
recommended free-first route**. The paid DeepSeek step needs OpenRouter credits;
check [OpenRouter pricing](https://openrouter.ai/pricing) and model limits before
sending campaign content. Free NVIDIA endpoints log prompts under their
published [usage terms](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free).
Connection tests use the current form values
without saving, make one bounded completion (up to a 120-second HTTP timeout
for Gemini and OpenRouter, 15 seconds for local providers, 2048
output tokens including reasoning; Gemini uses low-effort reasoning in tests
and normal generation), and return
sanitized status. They never fall back or write world state.

Before the first save, existing environment > `deploy/config.toml` > code defaults
continue to govern text generation. After saving, SQLite's singleton non-secret
profiles and routing override those deployment defaults and survive restarts.
API keys always come from the API process environment; no key is saved in SQLite,
returned by these routes, or included in frontend assets. Local authentication
uses the existing optional `MYTHOSCIRCLE_LLM_API_KEY`. The shipped local service
requires that key in Compose, independently of application routing selection.

To activate Gemini on the actual installation:

1. Create an API key in [Google AI Studio](https://aistudio.google.com/apikey).
   Keep it in the operator's secret environment; never paste it in chat,
   `config.toml`, repository files, or any frontend/Vite variable.
2. In the **API service `environment:` block** of the actual external stack
   `/home/homest/mythos-redeploy.yml`, add the following interpolation entry:
   ```yaml
   MYTHOSCIRCLE_GEMINI_API_KEY: ${MYTHOSCIRCLE_GEMINI_API_KEY:-}
   ```
3. In Portainer's stack **Environment variables**, set
   `MYTHOSCIRCLE_GEMINI_API_KEY` to the secret. A stack variable alone does not
   pass it into a container; the service environment entry above is also required.
   Keep the deployment runner's interpolation environment consistent with the
   external stack. The runner does not inherit Portainer's variables: supply the
   same secret through its protected environment/secret provisioning so automated
   redeploys do not remove the key. Do not commit the value.
4. Recreate the API container from that stack. Reload the admin panel and verify
   **Gemini key configured**. Select Gemini as primary (and Local as backup if
   wanted), run **Test Gemini connection**, then **Save provider settings**.

To activate OpenRouter, create a key in [OpenRouter Keys](https://openrouter.ai/settings/keys).
Add this entry to the **API service `environment:` block** of the same external
stack:

```yaml
MYTHOSCIRCLE_OPENROUTER_API_KEY: ${MYTHOSCIRCLE_OPENROUTER_API_KEY:-}
```

Set `MYTHOSCIRCLE_OPENROUTER_API_KEY` in Portainer stack variables and in the
deployment runner's protected interpolation environment. Recreate the API
container, reload the admin panel, confirm **OpenRouter key configured**, test
the connection, and save OpenRouter as primary or backup. Never put the key in
this repository, the browser, or the database.

Repository Compose templates include optional Gemini and OpenRouter passthrough,
but updating them does not update the externally managed stack. Replacing or
removing an API key requires recreating the API container. Before removing a
key, save routing that does not select its provider.

Routing is snapshotted when each job starts. Running jobs retain their provider
settings; subsequent jobs use newly saved settings. A connection/timeout error,
HTTP 429, or HTTP 5xx tries the enabled backup once. HTTP 4xx other than 429,
empty/malformed content, and truncated output retain existing failure/repair
behavior. Each physical generation attempt reserves one existing job-budget
slot before HTTP; the backup cannot exceed that budget. The backup keeps the
current call's schema, output-token and sampling adjustments. Both attempts are
recorded in the existing private job journal with their actual provider/model.
Image and video routing are unchanged.

Gemini quotas and rate limits depend on the AI Studio project and billing tier;
free availability is not guaranteed. Free-tier prompts/responses may be used to
improve Google products. Review [Google's pricing and data-use terms](https://ai.google.dev/gemini-api/docs/pricing)
and [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) before
sending campaign content. Real Gemini connectivity remains unverified until the
operator installs a key and explicitly tests it.

Publish through a feature PR into `develop` with the required **CI gate**, then
release through the normal `develop` → `main` PR. Production activation uses the
operator-managed secret and normal release process; never deploy a development
branch or bypass the checks.

### FastVideo FastH3 image-to-video workflow

`deploy/workflows/video_fastvideo_fasth3_i2v.json` preserves the supplied FastVideo
FastH3 workflow. It uses the 8-step v2 INT8 diffusion model, a 15-second duration,
24 fps and a 0.4-megapixel scaled portrait. The existing ComfyUI provider supports
it through configuration; no adapter change or production backend switch is required.

For a local development API, set these environment variables before starting it:

```bash
export MYTHOSCIRCLE_VIDEO_BACKEND=comfyui
export MYTHOSCIRCLE_COMFYUI_VIDEO_ENDPOINT=http://127.0.0.1:7896
export MYTHOSCIRCLE_COMFYUI_VIDEO_WORKFLOW_PATH=/absolute/path/to/mythosCircle/deploy/workflows/video_fastvideo_fasth3_i2v.json
export MYTHOSCIRCLE_COMFYUI_VIDEO_PROMPT_NODE_ID=105:104
export MYTHOSCIRCLE_COMFYUI_VIDEO_FIRST_FRAME_NODE_ID=136
export MYTHOSCIRCLE_COMFYUI_VIDEO_INPUT_DIR=/absolute/path/to/ComfyUI/input
```

The actual local ComfyUI instance uses `/home/main/ComfyUI-Shared/input`.
The provider replaces the demonstration prompt and source image with the reviewed
reveal prompt and portrait, stages a temporary first frame, fetches the saved MP4,
and removes its staged input afterwards. Node `92` saves video. Leave the other
workflow settings intact. Both endpoints and input paths must be reachable from
the API process; containers require their own mounted input directory and host URL.
Do not copy this workstation's absolute paths into a production stack.

The former MiniMax workflow remains available. To select it again, restore its
workflow path and first-frame node `114` together. These overrides affect only
new video calls after the API restarts; image and text routing are independent.
Release this artifact through the normal checked `develop` → `main` process
before activating it in production.

### Portrait composition correction

The portrait/headshot directives now describe one continuous composition rather
than assigning the face to an upper image region. The previous headshot wording
led the ComfyUI prompt refiner to place the background in a separate lower half,
producing stacked, misaligned crops in a cartoon render. Both Krea2 workflows
now explicitly preserve a single composition and continuous background during
refinement. Appearance text, style choice, sampler settings and seed are retained.
When running from a separate checkout, point both image workflow overrides at
that checkout's files so the API does not keep reading an older operator config's
workflow directory:

```bash
export MYTHOSCIRCLE_COMFYUI_IMAGE_WORKFLOW_PATH='/absolute/path/to/mythosCircle/deploy/workflows/image_krea2_turbo_t2i_int8 (2).json'
export MYTHOSCIRCLE_COMFYUI_IMAGE_REMBG_WORKFLOW_PATH=/absolute/path/to/mythosCircle/deploy/workflows/image_krea2_turbo_t2i_int8_with_background_removal.json
```

The reported cartoon portrait was verified with the same appearance, style,
headshot/scene options and workflow seed. A new campaign image completed through
the running queue with a continuous composition; previous media remain in history.
