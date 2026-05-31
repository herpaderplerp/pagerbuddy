# PagerBuddy Agent Notes

Keep this file updated whenever project structure, commands, deployment assumptions, auth, or external integrations change.

## Project Shape

PagerBuddy is a FastAPI service for Twilio-based on-call incident management.

- App package: `src/pagerbuddy`
- Admin UI: `src/pagerbuddy/ui`
- Tests: `tests`
- Container orchestration: `docker-compose.yml`
- Runtime config template: `.env.example`
- Local secrets: `.env` is ignored and must not be committed

## Run And Verify

Use the existing virtualenv for local checks:

```bash
.venv/bin/pytest -q
python3 -m compileall src tests
PYTHONPATH=src .venv/bin/python -c "from pagerbuddy.main import app; print(app.title, len(app.routes))"
```

Rootless Podman is the active local container runtime:

```bash
podman compose up -d
podman compose ps
podman compose logs --tail=80 app worker scheduler db
```

The dashboard is served at:

```text
http://localhost:8000/dashboard
```

## Authentication

Admin surfaces require HTTP Basic auth:

- `/dashboard`
- `/docs`
- `/openapi.json`
- admin REST paths such as `/users`, `/services`, `/incidents`

Public paths:

- `/healthz`
- `/webhooks/twilio/...`
- `/dashboard/assets/...`
- `/incident-actions/...`

Authentication accepts either the bootstrap admin configured by `ADMIN_USERNAME`/`ADMIN_PASSWORD` or an active database user logging in with email address and password.

Role enforcement:

- `admin`: full dashboard/API management, including users, policies, services, schedules, and stakeholder subscriptions.
- `responder`: operational incident actions and read access to operational configuration.
- `stakeholder`: read-only operational access.

If `ADMIN_PASSWORD` is blank, the bootstrap admin is disabled; database users with passwords can still authenticate. The current local ignored `.env` may contain development credentials, but do not copy secrets into tracked files.

## Twilio And ngrok

Twilio webhook validation is enabled by default with:

```env
TWILIO_VALIDATE_REQUESTS=true
```

`PUBLIC_BASE_URL` must exactly match the public webhook URL configured in Twilio, because Twilio signs the external URL.

Inbound caller restriction is opt-in:

```env
INBOUND_CALLER_WHITELIST_ENABLED=true
INBOUND_CALLER_WHITELIST_NUMBERS=+15551234567,+15557654321
```

When enabled, `/webhooks/twilio/voice` rejects callers not in the comma-separated whitelist before recording. Rejected calls create a `SystemEvent` with `event_type="inbound_call_rejected"` and no incident.

Current local development uses ngrok. The tunnel is intended to run detached in a screen session:

```bash
screen -ls
screen -r pagerbuddy-ngrok
screen -S pagerbuddy-ngrok -X quit
```

When the ngrok URL changes:

1. Update ignored `.env` `PUBLIC_BASE_URL`.
2. Recreate app and worker:

   ```bash
   podman compose up -d app worker
   ```

3. Update the Twilio number callbacks:
   - Voice: `<PUBLIC_BASE_URL>/webhooks/twilio/voice`
   - SMS: `<PUBLIC_BASE_URL>/webhooks/twilio/sms`

The local Twilio trial guard restricts phone/SMS notifications to `TWILIO_TRIAL_ALLOWED_NUMBER` when configured.

## Local Recordings

Voicemail recordings can be downloaded during `/webhooks/twilio/recording-complete` when:

```env
STORE_RECORDINGS_LOCALLY=true
RECORDING_STORAGE_DIR=/app/recordings
LOCAL_TRANSCRIPTION_ENABLED=true
LOCAL_TRANSCRIPTION_MODEL=base.en
LOCAL_TRANSCRIPTION_DEVICE=cpu
LOCAL_TRANSCRIPTION_COMPUTE_TYPE=int8
```

Compose mounts ignored `./recordings` into `/app/recordings`. Recording files must not be committed.

When local transcription is enabled, inbound Twilio `<Record>` disables Twilio transcription and PagerBuddy transcribes the downloaded local file with `faster-whisper` before escalation starts. Outbound responder calls still play Twilio's hosted recording media URL via `<Play>`; local recording serving is not part of the current implementation.

## Secrets

Never commit secrets. `.env` is ignored. Keep `.env.example` limited to blank/example values only.

Ignored local artifacts include:

- `.env`
- `.venv/`
- `.pytest_cache/`
- `__pycache__/`
- `*.egg-info/`
- `.DS_Store`
- `pagerbuddy.db`
- local database dumps such as `*.sql`, `*.dump`, `*.sqlite`, and `*.db`
- `recordings/`

## Implementation Notes

- Database tables are currently created with `Base.metadata.create_all`; production should move to Alembic.
- The worker processes escalation timers.
- The scheduler checks schedule gaps and records/sends admin alerts.
- Twilio webhooks are under `src/pagerbuddy/twilio_webhooks.py`.
- Signature validation is in `src/pagerbuddy/twilio_security.py`.
- Notification dispatch and trial-recipient checks are in `src/pagerbuddy/notifications.py`.
- User notification preferences use `notification_preferences.channels`; every configured channel is sent for each escalation attempt rather than rotating one channel per retry.
- SMS notifications include incident IDs; inbound SMS accepts `ACK <incident ID>` and `RESOLVE <incident ID>` to disambiguate open incidents for the responder.
- Email action links expire after `INCIDENT_ACTION_TOKEN_TTL_SECONDS` unless set to `0`; used tokens and closed-incident tokens are still rejected.
- Recording downloads are in `src/pagerbuddy/recordings.py`.
- Local transcription is in `src/pagerbuddy/transcription.py`.
- Password hashing and RBAC dependencies are in `src/pagerbuddy/auth.py`.
- Prefer disabling users over deleting referenced users. `/users/{user_id}/disable` preserves history, blocks login, and prevents the user from being paged by escalation. The API rejects disable requests while the user is still a direct escalation-policy contact or catchall.
- `Base.metadata.create_all` is supplemented by a small compatibility schema check for user-management columns while the project does not yet use Alembic.
- Admin dashboard JavaScript calls the same REST API and must keep using same-origin authenticated requests.
- The dashboard is intended to be the primary management surface. When admin REST endpoints are added or changed, expose the action in `src/pagerbuddy/ui` as well.
- Current dashboard management coverage includes create/list/update/delete for users, services, schedules, and escalation policies; stakeholder subscribe/unsubscribe; schedule gap checks; and incident create/update/escalation/acknowledge/resolve/reopen/reassign/merge/note/timeline actions.

## Current Gaps From Original Spec

Known remaining work includes:

- In-flight Twilio outbound call cancellation after acknowledgement.
- Alembic migrations.
