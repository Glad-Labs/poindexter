# Local storage provider (`storage_provider=local`)

A fresh install has no object store. Until this mode existed, every upload
logged `No object-store credentials … skipping upload` and returned `None`. You
could approve and publish a post and it would exist only as a `posts` row: no
static export, no images, nothing a browser could open. The first thing a new
user had to do to see a result was provision a bucket and paste credentials.

With `storage_provider=local`, which is the fresh-install default, those same
uploads go to a folder, and the worker serves that folder at
`http://localhost:8002/site/` with a small reader. Queue a post, approve it,
publish it, and open the page.

```bash
poindexter tasks list --status awaiting_approval
poindexter tasks approve <id>
poindexter tasks publish <id>
# then open http://localhost:8002/site/
```

## Settings

| Key                      | Default                 | Meaning                                                                                                                                                                                              |
| ------------------------ | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `storage_provider`       | `local`                 | `local`: uploads go to `storage_local_dir`. `s3`: uploads go to the S3-compatible bucket in `storage_*`. A missing or blank row means `s3`; an unknown value is logged at ERROR and treated as `s3`. |
| `storage_local_dir`      | `~/.poindexter/site`    | The folder. `~` expands per process. Inside the worker containers it is the `poindexter-site` volume.                                                                                                |
| `storage_public_url`     | empty                   | URL the folder is served under, when it is somewhere other than the site URL.                                                                                                                        |
| `api_url`                | `http://localhost:8002` | The worker as your browser reaches it. The preview lives at `{api_url}/site`.                                                                                                                        |
| `site_url` / `site_name` | filled at boot          | See [Site identity](#site-identity).                                                                                                                                                                 |

Switch at runtime with `poindexter settings set storage_provider <local|s3>`. No
restart is needed: the uploader and the `/site` mount read the setting on
every call.

## How it works

### One seam

Every uploader, reaper and feed builder already goes through
`R2UploadService` (`services/r2_upload_service.py`). Each public method first
asks `_local()` whether the install is in local mode. If it is, the call goes
to a `LocalObjectStore` (`services/local_object_store.py`). If not, the S3 code
runs unchanged. None of the roughly thirty callers changed, and the S3 path
that publishes production sites is byte-for-byte the same code apart from one
extracted helper (`_webp_key`).

The local store keeps the S3 contract:

- **Uploads** get the same preparation as S3: content-type detection, WebP
  conversion, the `.png` → `.webp` key rewrite, and the custom image domain. A
  post's objects therefore have the same keys and the same kind of URL on
  either backend. A test pins the key rewrite for both.
- **Writes are atomic.** Each file is written to a temp file beside its
  target and moved into place with `os.replace`. The viewer can read
  `static/posts/index.json` while a publish rewrites it and never sees half a
  file.
- **Listing uses S3 prefix semantics.** `list_objects("static/posts/ind")`
  answers the way S3 would, sorted by key.
- **`object_size` keeps its tri-state.** An absent file is `None`. An
  unreadable folder raises `ObjectStoreUnavailable`, so `media_reconciliation`
  never reads a permission error as "lost file, re-upload".

### Keys become paths, so keys are validated

An S3 key is an opaque string, but here a key becomes a path. `path_for`
refuses empty and absolute keys, NUL and backslash, empty / `.` / `..`
segments, and the temp-file prefix. Every key the codebase builds passes;
`test_local_object_store.py` lists them.

### Where objects are served

The URL prefix for local objects is the first non-empty value of:

1. `storage_public_url`: you serve the folder somewhere else.
2. `public_site_url`, then `site_url`: in local mode the folder is the site.
3. `{api_url}/site`: the preview.

Keeping objects under the site's own origin is also what stops
`qa.citations` from probing them. The citation verifier skips URLs on
`site_url`'s origin as internal, and `![alt](url)` images match its link
pattern.

### Readers that hold a URL read the file instead

`{api_url}/site/…` is the URL a browser uses. From inside the Prefect
container, `localhost:8002` is the container itself, not the worker. Vision QA
(`multi_model_qa._check_image_relevance`) and the image captioner
(`image_captioner._fetch_b64`) therefore call `local_site.read_local_object`
first. It maps a URL under the local prefix back to its file on the shared
volume, and returns `None` for anything else so those callers fall back to
HTTP. Without this, vision QA would find no fetchable images on any post in
local mode and silently stop producing verdicts.

### The `/site` mount

`utils/local_site_mount.py` mounts `LocalSiteApp` at `/site`, after the API
routers so it can't shadow `/api`. It is registered unconditionally and checks
the mode per request:

| Request                                     | Answer                                                                                                                     |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| any, while `storage_provider` isn't `local` | 404 with the reason and the command to switch                                                                              |
| `/site/_viewer/viewer.js`, `viewer.css`     | the viewer's assets, shipped in the package (`poindexter/local_site_viewer/`) so upgrades apply at once                    |
| a file in the folder                        | that file, after key validation and a `realpath` check that it is a regular file inside the folder (symlinks can't escape) |
| `/site/`, `/site/posts/<slug>`              | the viewer page. Post links in the export are `{site_url}/posts/<slug>`, which is this route in local mode                 |
| anything else                               | 404, never an HTML page, so a missing image doesn't come back as a document                                                |

The viewer (`index.html`, `viewer.js`, `viewer.css`) has no dependencies and no
build step. It reads `static/posts/index.json`, `static/posts/<slug>.json` and
`static/manifest.json`, which now carries `site_name`.

**Security.** The page shares an origin with the worker API and, on installs
that have it, the operator console, while post HTML is written by an LLM
working from web research. Every response carries a Content-Security-Policy
with `script-src 'self'` and no inline script, so markup injected into a post
can't run. The viewer also removes active content before inserting a post
(`script`, `iframe`, `object`, `form`, `on*` attributes, `javascript:` URLs).
The mount is read-only (GET and HEAD) and public, like the public bucket it
stands in for. Everything in the folder, including images for drafts not yet
approved, is readable by anyone who can reach the worker's port. That is the
same exposure a public bucket has.

### Site identity

Publishing calls `site_config.require("site_url")`, and the static export also
requires `site_name`. The reference seed leaves both empty and `poindexter
setup` never asks for them. Two things fill them:

- **The brain daemon**, on the Docker stacks. It boots before the worker and
  refills every empty value from its free-tier seed
  (`poindexter/brain/seed_app_settings.json`). That seed used to set
  `site_url` to `http://localhost:3000`, a frontend that isn't in the public
  repo and, on the consumer stack, the port Grafana listens on. So post links,
  feeds and sitemaps pointed at Grafana. The seed now points `site_url` and
  `public_site_url` at `http://localhost:8002/site`, which is this preview.
- **The worker**, where no brain runs (for example, the backend started on the
  host with `npm run dev`). Identity would otherwise stay empty and the first
  publish would raise after the post row was written. At boot, after the
  defaults and the operator overlay are seeded,
  `local_site.fill_local_site_identity` fills an empty `site_url` with
  `{api_url}/site` and an empty `site_name` with `My Content Site`: the same
  values the brain seeds. A test keeps the two equal. It fills empty values
  only, never runs in `s3` mode (where an unset `site_url` should keep failing
  loudly), and reads the database directly because it runs before settings
  load into memory.

### Consumer stack

`docker-compose.consumer.yml` mounts a named volume, `poindexter-site`, at
`/home/appuser/.poindexter/site` on both `worker` and `prefect-worker`. Images
are stored during the flow run, while the export and the viewer run in the
worker, so both containers need it. It is a named volume rather than a host
bind mount for two reasons. Docker creates a missing bind-mount source as root,
which the uid-1001 worker can't write into. And a named volume exposes nothing
of the host's `~/.poindexter`, which holds `bootstrap.toml` and, on an operator
host, files the host executes. Keep it named:
`scripts/ci/compose_poindexter_home_mount_lint.py` exempts named volumes, but
would reject a bind mount of `~/.poindexter/site` as an undeclared entry.
`Dockerfile.worker` pre-creates the directory owned by `appuser`, and Docker
seeds a new named volume from it, ownership included.

## Upgrading an existing install

An install that already has a bucket must keep using it. Migration
`20260928_174425_pin_storage_provider_to_s3_on_installs_with_a_configured_object_store`
writes `storage_provider=s3` when any object-store key (`storage_*` or the
legacy `cloudflare_r2_*`) has a value. Migrations run before the defaults are
seeded, so the seeder's `ON CONFLICT DO NOTHING` then leaves the row alone. The
migration also upgrades a row that was seeded `local` first. The key list is
derived in a test from what `R2UploadService` reads, not typed out twice.

Code reads a missing row as `s3` as well. That covers the window after a code
deploy in which a Prefect flow run can load new code before the worker has
rebooted and run the migration.

## Switching providers

**To S3.** Set the bucket keys (`storage_endpoint`, `storage_bucket`,
`storage_access_key`, the `storage_secret_key` secret, `storage_public_url`),
set `site_url` to your real site, run `poindexter settings set
storage_provider s3`, and rebuild the export (`POST /api/export/rebuild`,
authenticated like the rest of the API) so the bucket has every post. Objects
already in the folder are not copied.

**To local on an install that has S3.** Run `poindexter settings set
storage_provider local`. Objects go under your `site_url` unless
`storage_public_url` says otherwise. To read them in the preview, set
`storage_public_url` to `{api_url}/site`.

## Limits

- **One machine.** The volume is the only copy of stored images; the JSON
  export rebuilds from Postgres, the images don't. Back the volume up if you
  keep them.
- **Channels that need a public URL** (newsletter, social posts, podcast and
  video feeds read by outside apps, YouTube thumbnails) still need S3 and a
  public URL. Local mode serves a preview on your machine, not the internet.
- **The Stage-2 video renderer** downloads post images over HTTP from the flow
  container, and in local mode those downloads fail. Use S3 if you render
  video.
- **The operator stack** (`docker-compose.local.yml`) doesn't mount the
  volume. Local mode is for the consumer stack.
- **It is a reader, not a static site generator.** The viewer renders the
  JSON in the browser and the page is `noindex`.

## Verifying

```bash
curl -s http://localhost:8002/site/static/manifest.json     # after the first publish
poindexter settings get storage_provider
```

A 404 reading "The local site is off" means `storage_provider` isn't `local`.
The worker logs `[STORAGE] Stored locally: <url>` for each object and
`[STORAGE] Local write failed …` with the folder path when it can't write.

Tests: `tests/unit/services/test_local_object_store.py`, `test_local_site.py`,
`test_r2_upload_service_local.py`, `test_vision_qa_local_images.py`,
`tests/unit/utils/test_local_site_mount.py`, the migration's unit test, and
`tests/integration_db/test_local_storage_provider.py`. The last one exports a
real published post to a folder and serves it back through `/site`.
