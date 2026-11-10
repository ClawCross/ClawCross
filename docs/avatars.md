# User and Agent avatars

The Me page edits the signed-in user's avatar. Agent settings expose a separate
Avatar entry. Both support PNG, JPEG, WebP, SVG uploads, and a description that
generates an SVG preview. Saving is explicit; cancelling does not update identity.
The browser crops raster uploads to 256×256. Saved images may be at most 64 KiB
and 512×512; SVGs may be at most 12,000 UTF-8 bytes.

## Generation

`agents/avatar_generation.py` calls `common.llm_factory.create_chat_model` once
with the existing main API/model configuration and an avatar-specific system
prompt. It does not create a conversation or Agent, save the preview, or add the
image to a model's user portrait. The lowest supported reasoning setting reserves
the bounded 4096-token output for the SVG. Provider defaults must not consume
that budget entirely as hidden reasoning.

The call has no automatic retries, a 45-second provider timeout and a 50-second
outer timeout. Limits are three requests per user per minute, one in flight per
user, and four in flight in the API process. Browser invitation credentials do
not grant access to the host's model-generation API.

## Authenticated APIs

| Method | Path | Result |
|---|---|---|
| GET | `/v1/agents/avatars/me` | Current user's reference, URL and configured flag |
| PUT | `/v1/agents/avatars/me` | Save `avatar`, or clear with an empty string |
| POST | `/v1/agents/avatars/generate` | `description`, optional `name`, `kind` → SVG preview and URL |
| GET | `/v1/agents/{id}/avatar` | Owned Agent's reference and URL |
| PATCH | `/v1/agents/{id}` | Save through `settings.avatar` |

Saved identities use `av_<sha256>` references. Image payloads live in the owner's
hashed directory under `USER_FILES_DIR/.avatars`, outside user portrait files.
Agent cards contain references, so image bytes do not inflate list responses.
The storage rejects foreign references, unsafe paths, external SVG assets, scripts,
event handlers and unsafe animations. SVG renders through an image element.

## Group sharing

The group relay stores assets separately from member metadata. A group member
can read an image through `/groups/{id}/members/{principal}/avatar/{image_id}`
or the equivalent guest endpoint only while authorized for that group and while
the asset belongs to the indicated current member.

Local avatar changes synchronize independently of chat delivery and Agent activity.
The first check runs after two seconds; subsequent successful checks run every
15 seconds, with failure backoff of 30–300 seconds. Only changed references are
published. An unset local avatar leaves an existing group avatar intact; an
explicit clear propagates. This process never invokes a model or sends a message.

## Verification

`test/test_avatars.py` covers validation, ownership, preview generation and
cross-group access. Avatar cases in `test/browser/group-chat-features.spec.js`
cover preview/cancel/save, user upload, narrow viewports, direct conversation
portraits and historical message refresh. Viewport emulation is not a physical
iPhone Safari test.
