# ClawCross Creator

Use this page when you want to build a Team from a business task, discovered SOP pages, or an existing workflow canvas.

## Entry Points

- `GET /creator`: standalone ClawCross Creator page
- `mobile_group_chat`: ClawCross Creator card in the discover area
- `GET /studio`: workflow canvas toolbar button `Generate Team`

## When To Use It

| Goal | Best Entry |
|---|---|
| Start from a plain-language task and let the system discover roles | ClawCross Creator discovery mode |
| Paste or hand-edit a few known roles | ClawCross Creator direct mode |
| Start from an existing workflow canvas and bulk-create a Team | `Generate Team` in Studio |
| Edit a Team completely through files or scripts | `docs/build_team.md` + CLI |

## Main Flow

### 1. Direct Role Definition

Direct mode lets the operator:

- set `team_name` and `task_description`
- add roles manually or paste role JSON
- pull preset experts from the shared expert pool
- keep preset experts bound to their full persona prompt instead of flattening them into short fields

Use this path when you already know the role list.

### 2. Discovery and Extraction

Discovery mode is a three-part pipeline:

1. `POST /api/team-creator/discover`
   - streams SSE events while the internal LLM finds SOP / org-structure URLs
   - can fall back to TinyFish browser search when needed
2. `POST /api/team-creator/extract`
   - streams SSE events while TinyFish extracts role data from each page
   - the UI shows per-page tabs, live logs, and browser previews
3. `POST /api/team-creator/smart-select`
   - ranks the extracted roles
   - matches them against the preset expert pool

This path is the best fit when the user starts with a business scenario rather than a finished team design.

### 3. Build, Preview, and Export

`POST /api/team-creator/build` converts the selected roles into a Clawcross team config:

- `oasis_experts`
- Team member config
- OASIS workflow YAML
- DAG preview data

The page then shows:

- team summary stats
- editable persona cards
- `OASIS Workflow DAG`
- YAML source
- ZIP download and one-click import into Clawcross

`POST /api/team-creator/download` uses the same snapshot-style ZIP format as the regular Team export flow.

### 4. Import Colleague / Mentor Skills

ClawCross Creator also supports browser-native import flows for external role distillation projects:

- `colleague-skill` → import `meta.json + persona.md + work.md`
- `supervisor` / `distill-mentor` → import `{name}.json + SKILL.md`

Both flows support:

- file upload from the browser
- local path input for files already generated on the same machine as Clawcross
- direct in-app generation without touching the upstream repos:
  - `ArXiv -> mentor JSON -> import mentor`
  - `Feishu -> messages -> auto distill persona/work -> import colleague`

Import uses the upstream artifact files as the source of truth. The full upstream `persona.md` / generated mentor `SKILL.md` is preserved as the final Clawcross expert persona instead of being compressed into the short `_build_persona()` summary.

### 5. Python-Native Quick-Create Helpers

Two backend helpers already cover the Phase 3 collection step without relying on the upstream Node.js tools:

- `POST /api/team-creator/arxiv-search`
  - searches ArXiv in pure Python
  - returns a supervisor-compatible mentor JSON
  - can optionally auto-import that JSON into ClawCross Creator
  - this is the backend used by the ClawCross Creator "搜索 ArXiv 并生成导师" button
- `POST /api/team-creator/feishu-collect`
  - collects Feishu messages in pure Python
  - returns colleague-compatible `meta.json` plus the collected message corpus
  - can optionally auto-distill `persona.md + work.md`
  - can optionally auto-import the distilled colleague directly into ClawCross Creator
  - this is the backend used by the ClawCross Creator "采集并生成同事" button

## Bilingual and Dynamic Translation Behavior

ClawCross Creator has two layers of localization:

- static UI strings are embedded in `frontend/js/creator.js`
- dynamic content can be translated on demand through `POST /api/team-creator/translate`

Dynamic translation is used for things like:

- discovered role names
- expert summaries
- persona snippets and other generated text

The preset expert pool uses the same bilingual data source as the message center expert browser. When a preset expert is added into `Define Team Roles`, the UI should keep the full preset persona attached to that role.

## Persistence and Build History

ClawCross Creator persists state in two places:

- browser draft state: `window.sessionStorage` key `clawcross_creator_session_v1`
- server build history: `data/team_creator_jobs.db`

Relevant job APIs:

- `GET /api/team-creator/jobs`
- `GET /api/team-creator/jobs/<job_id>`

The job record keeps the task description, extracted roles, build status, and the generated `team_config`.

If needed, set `TEAM_CREATOR_JOBS_DB_PATH` to move the jobs database.

## Workflow -> Generate Team

The Studio workflow canvas also supports a direct Team-generation flow through `orchGenerateTeam()` in `frontend/js/orchestration.js`.

That flow:

- scans canvas nodes of type `expert`, `session_agent`, and `external`
- deduplicates them by `tag`
- lets the user create a new Team or target an existing one
- checks tag conflicts against the target Team
- lets the user choose `skip` or `overwrite` per conflict
- submits the final payload to `POST /teams/<team_name>/generate-from-workflow`

Use this when the workflow graph already exists and you want to materialize the corresponding Team quickly.

## API Quick Reference

| Route | Purpose |
|---|---|
| `GET /creator` | render the ClawCross Creator page |
| `POST /api/team-creator/import-colleague` | import `colleague-skill` artifacts into ClawCross Creator |
| `POST /api/team-creator/import-mentor` | import `supervisor` mentor artifacts into ClawCross Creator |
| `POST /api/team-creator/arxiv-search` | generate mentor JSON from ArXiv search, optionally auto-import |
| `POST /api/team-creator/feishu-collect` | collect Feishu messages, optionally auto-distill persona/work, optionally auto-import |
| `POST /api/team-creator/discover` | stream discovery events for relevant pages |
| `POST /api/team-creator/extract` | stream extraction events for one page |
| `POST /api/team-creator/smart-select` | rank extracted roles and match presets |
| `POST /api/team-creator/build` | build the final team config |
| `POST /api/team-creator/download` | export the built config as ZIP |
| `POST /api/team-creator/translate` | dynamic bilingual translation |
| `GET /api/team-creator/presets` | backward-compatible preset list |
| `GET /api/team-creator/jobs` | list recent ClawCross Creator build jobs |
| `GET /api/team-creator/jobs/<job_id>` | load one saved build |
| `POST /teams/<team_name>/generate-from-workflow` | create or extend a Team from workflow nodes |

## Relevant Files

| Path | Role |
|---|---|
| `src/front.py` | ClawCross Creator routes and workflow-to-team endpoint |
| `src/services/team_creator_service.py` | discovery, extraction, build, ZIP, jobs, translation |
| `frontend/templates/creator.html` | ClawCross Creator page shell |
| `frontend/js/creator.js` | ClawCross Creator UI, i18n, persistence, preview rendering |
| `frontend/css/creator.css` | ClawCross Creator layout and DAG styling |
| `frontend/js/orchestration.js` | `Generate Team` modal on the workflow canvas |
| `test/test_team_creator_jobs.py` | jobs persistence coverage |
| `test/test_team_creator_workflow.py` | workflow build coverage |
| `test/test_team_creator_zip.py` | ZIP export coverage |
| `test/test_proxy_login_i18n.py` | i18n-related frontend proxy coverage |

## Related Docs

- [build_team.md](./build_team.md)
- [create_workflow.md](./create_workflow.md)
- [oasis-reference.md](./oasis-reference.md)
- [repo-index.md](./repo-index.md)
- [runtime-reference.md](./runtime-reference.md)
