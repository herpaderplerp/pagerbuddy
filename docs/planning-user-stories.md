# PagerBuddy Planning User Stories

This document translates the current PagerBuddy implementation into planning stories and highlights missing functionality for future development. It is based on the current FastAPI service, dashboard, Twilio webhooks, worker, scheduler, and tests.

## Current Product Scope

PagerBuddy currently supports:

- Twilio inbound voicemail incident creation for configured services.
- Local recording download and optional local transcription.
- Escalation policies targeting users or schedules, with retries, repeats, and catchall users.
- Phone, SMS, and email responder notifications.
- SMS commands for `ACK <incident ID>` and `RESOLVE <incident ID>`.
- Email action tokens for acknowledge and resolve.
- Incident lifecycle actions: create, update, start escalation, acknowledge, resolve, reopen, reassign, merge, add notes, and view timeline.
- User, service, schedule, escalation policy, and stakeholder subscription management.
- HTTP Basic authentication with admin, responder, and stakeholder roles.
- Schedule overrides and schedule gap detection.
- Twilio request signature validation and optional inbound caller whitelist.

## User Stories

### Incident Intake

1. As a caller, I want to leave a voicemail for the service on-call line so the right team can be paged with enough context.
   - Acceptance criteria: A call to a configured inbound number records voicemail, creates a `P2` incident, stores caller/recording metadata, notifies stakeholders, and starts escalation.

2. As an administrator, I want to restrict inbound incident creation to approved caller numbers so untrusted callers cannot create incidents.
   - Acceptance criteria: When caller whitelist mode is enabled, rejected calls do not create incidents and are recorded as system events.

3. As a responder, I want manual incident creation from the dashboard/API so I can page the on-call process for non-phone incidents.
   - Acceptance criteria: A responder or admin can create an incident with service, title, priority, caller/recording/transcription context, and optional escalation start.

4. As a responder, I want voicemail transcription attached to incidents so I can triage without listening to the recording.
   - Acceptance criteria: Twilio or local transcription updates the incident, records a timeline event, and sends a follow-up notification to the currently notified responder.

### Escalation And Notification

5. As an administrator, I want to configure escalation policies with users, schedules, retries, repeats, and catchall responders so incidents follow a predictable path.
   - Acceptance criteria: Policies can target users or schedules; failed or unanswered attempts advance after the configured timeout; exhausted policies can notify a catchall user.

6. As a responder, I want all configured notification channels to fire for an escalation attempt so urgent incidents reach me through phone, SMS, and email.
   - Acceptance criteria: PagerBuddy dispatches every enabled channel, records each notification attempt, and marks provider failures from Twilio status callbacks.

7. As a responder, I want quiet hours to reduce non-urgent interruptions so lower-priority incidents use less disruptive channels overnight.
   - Acceptance criteria: During quiet hours, non-overridden priorities only send email, while configured priority overrides still use all preferred channels.

8. As a responder, I want phone-call prompts to let me acknowledge or escalate immediately so I can act without opening the dashboard.
   - Acceptance criteria: Outbound calls play the incident context or voicemail, accept DTMF acknowledgement, and support immediate manual escalation.

9. As a responder, I want SMS actions to include the incident ID so I can safely act when multiple incidents are open.
   - Acceptance criteria: SMS notifications include the incident ID; inbound SMS accepts `ACK <incident ID>` and `RESOLVE <incident ID>`; ambiguous commands ask for an ID.

10. As a responder, I want email links to acknowledge or resolve incidents without signing in so I can respond quickly from email.
    - Acceptance criteria: Email notifications include tokenized action links; tokens expire by TTL, are single-use, and are rejected after incident closure.

### Incident Operations

11. As a responder, I want to acknowledge an incident so escalation stops and ownership is recorded.
    - Acceptance criteria: Acknowledgement sets status, assigned user, acknowledged timestamp, clears the next escalation time, and records timeline events.

12. As a responder, I want to resolve an incident so stakeholders and the team know the issue is closed.
    - Acceptance criteria: Resolution sets resolved status/time, stops escalation, records a timeline event, and notifies subscribed stakeholders.

13. As a responder, I want to reopen an acknowledged incident so a still-active issue can re-enter escalation.
    - Acceptance criteria: Reopen clears acknowledgement/assignment fields, resets escalation counters, records a timeline event, and restarts escalation.

14. As a responder, I want to reassign an incident so the right person owns follow-up.
    - Acceptance criteria: Reassignment changes the assignee, records a timeline event, and notifies the new assignee.

15. As a responder, I want to merge duplicate incidents so related calls do not create parallel response threads.
    - Acceptance criteria: Child incidents move to merged status, point to the parent incident, stop escalation, and record timeline events.

16. As a responder, I want to add notes to an incident timeline so operational context and status updates are preserved.
    - Acceptance criteria: Notes store author, body, status-update flag, actor, and timestamp in the immutable timeline.

### On-Call Scheduling

17. As an administrator, I want to define schedules with layers and rotations so escalation can page the current on-call user.
    - Acceptance criteria: Schedule layers support daily, weekly, and custom-hour rotations, weekday/time restrictions, and ordered layer resolution.

18. As an administrator, I want to add temporary overrides so planned coverage changes do not require rewriting the base schedule.
    - Acceptance criteria: Overrides require start/end times, replacement user, creator, and reason; overlapping overrides are rejected.

19. As an administrator, I want schedule gap detection so uncovered windows are visible before incidents happen.
    - Acceptance criteria: The API and dashboard can report gaps, and the scheduler records system events and emails admins when future gaps are detected.

### Administration And Access Control

20. As an administrator, I want to manage users and roles so only appropriate people can administer PagerBuddy or act on incidents.
    - Acceptance criteria: Admins can create, update, enable, disable, and delete users, while last-admin and referenced-contact guardrails prevent lockout or broken policies.

21. As an administrator, I want responders and stakeholders to have least-privilege access so operational data is visible without exposing management actions.
    - Acceptance criteria: Admins have full access, responders can read operational configuration and perform incident operations, and stakeholders have read-only operational access.

22. As an administrator, I want to manage services and inbound numbers so different teams can route calls to different escalation policies.
    - Acceptance criteria: Services require a unique inbound number and escalation policy, and can be created, listed, updated, and deleted when not referenced.

23. As an administrator, I want to subscribe stakeholders to service updates so business or customer-facing users receive triggered/resolved notifications.
    - Acceptance criteria: Only stakeholder-role users can be subscribed; duplicate subscriptions are rejected; unsubscribe removes future notifications.

24. As an administrator, I want the dashboard to expose core management actions so I do not have to use raw API calls for daily operations.
    - Acceptance criteria: Dashboard supports the same primary actions as the REST API for users, services, schedules, escalation policies, stakeholders, and incidents.

## Missing Functionality And Future Backlog

### Production Readiness

1. Alembic migrations.
   - Gap: Tables are created with `Base.metadata.create_all`, with a small compatibility check for user columns.
   - Why it matters: Production upgrades need explicit, reversible schema migrations and data backfills.

2. In-flight Twilio outbound call cancellation after acknowledgement.
   - Gap: Acknowledgement stops future escalation timers but does not cancel already-ringing outbound calls.
   - Why it matters: Other responders may keep receiving calls for an incident that has already been acknowledged.

3. Deployment hardening.
   - Gap: Runtime is Docker/Podman friendly, but there is no documented production deployment pattern, backup/restore procedure, health/readiness split, or secret-management workflow.
   - Why it matters: Operators need a reliable path from local development to a durable hosted deployment.

4. Observability.
   - Gap: There is basic logging and timeline/system events, but no metrics, tracing, structured log correlation, or admin-facing provider health view.
   - Why it matters: Incident-management software needs fast diagnosis when notifications, webhooks, SMTP, Twilio, transcription, or workers fail.

5. Background job robustness.
   - Gap: Worker and scheduler are polling loops without persisted job leases, retry backoff, dead-letter state, or duplicate-work protection across multiple workers.
   - Why it matters: Scaling or restarting workers should not cause missed or duplicated escalation actions.

### Incident Workflow

6. Priority and routing rules for inbound calls.
   - Gap: All inbound calls create `P2` incidents.
   - Future story: As an administrator, I want per-service default priority and caller/input-based priority routing so urgent services can start at `P1` and lower-risk services can start lower.

7. Incident deduplication.
   - Gap: Duplicate incidents can be merged manually, but there is no automatic duplicate detection.
   - Future story: As a responder, I want likely duplicate incidents suggested or auto-linked so repeated calls do not create unnecessary escalation noise.

8. Incident search, filters, and pagination.
   - Gap: Incident listing returns all incidents ordered by creation time.
   - Future story: As an operator, I want filters for status, service, priority, assignee, time range, and caller so I can find relevant incidents quickly at scale.

9. Service-level incident policy.
   - Gap: Services map to an escalation policy and inbound number only.
   - Future story: As an administrator, I want service-level settings for default priority, recording/transcription behavior, stakeholder rules, and notification templates.

10. Post-incident reporting.
    - Gap: Timeline events exist, but there is no incident summary, export, duration analytics, or review workflow.
    - Future story: As an incident lead, I want a post-incident report generated from the timeline so follow-up work and response metrics are easy to review.

### Escalation And Scheduling

11. Rich escalation policy editor.
    - Gap: The dashboard uses JSON prompts for policy steps.
    - Future story: As an administrator, I want a guided policy builder with validation so I can configure schedules, users, timeouts, retries, repeats, and catchall without hand-editing JSON.

12. Rich schedule editor and calendar view.
    - Gap: The dashboard uses JSON prompts for schedule layers and overrides.
    - Future story: As an administrator, I want a calendar view and guided rotation editor so schedule coverage can be inspected and changed safely.

13. Escalation preview and simulation.
    - Gap: There is no preview of who would be paged for a sample incident/time.
    - Future story: As an administrator, I want to simulate escalation for a service and timestamp so policy and schedule changes can be validated before saving.

14. Override lifecycle management.
    - Gap: Overrides can be added but not individually edited, deleted, approved, or requested by responders.
    - Future story: As a responder, I want to request or cancel coverage overrides so temporary coverage changes are auditable and self-service.

15. Team or group targets.
    - Gap: Escalation steps target a single user or schedule.
    - Future story: As an administrator, I want escalation steps to notify teams or multiple responders so some incidents can fan out to a group.

### Notifications And Integrations

16. Notification templates.
    - Gap: SMS, email, and phone scripts are hardcoded.
    - Future story: As an administrator, I want configurable templates per service/channel so messages contain the right operational instructions.

17. Additional notification channels.
    - Gap: Supported channels are phone call, SMS, and email.
    - Future story: As a responder, I want Slack, Microsoft Teams, push notification, or webhook delivery so PagerBuddy fits existing incident workflows.

18. Provider delivery visibility.
    - Gap: Notification attempts are stored but not exposed as a dedicated dashboard view.
    - Future story: As an administrator, I want to see notification attempts, provider IDs, statuses, and errors so delivery problems can be diagnosed from the UI.

19. Local recording playback.
    - Gap: Local recordings are used for transcription and retention, while outbound calls still play Twilio-hosted media.
    - Future story: As a responder, I want dashboard playback or secure download of stored recordings so evidence remains available even if Twilio media expires.

20. Stakeholder notification preferences.
    - Gap: Stakeholders receive service triggered/resolved email through the shared email sender.
    - Future story: As a stakeholder, I want configurable notification preferences by service and event type so I only receive relevant updates.

### Security And Compliance

21. Session-based dashboard authentication.
    - Gap: Admin surfaces use HTTP Basic auth.
    - Future story: As an administrator, I want browser-friendly login/logout, password reset, and session expiry so user access is manageable and auditable.

22. Audit log for administrative changes.
    - Gap: Incident timeline is strong, but user/service/policy/schedule changes do not have a first-class audit trail.
    - Future story: As an administrator, I want audit history for configuration changes so operational changes can be reviewed after an incident.

23. Data retention controls.
    - Gap: Recordings, incidents, tokens, system events, and timelines do not have retention policies.
    - Future story: As an administrator, I want configurable retention and purge controls so PagerBuddy can meet storage and compliance requirements.

24. Stronger credential lifecycle.
    - Gap: Passwords can be set on users, but there is no password reset, invite flow, forced rotation, MFA, or account lockout.
    - Future story: As an administrator, I want a safer credential lifecycle so compromised or forgotten credentials can be handled without direct database edits.

### Dashboard Experience

25. Replace prompt-based editing with structured forms.
    - Gap: Several dashboard edits use browser prompts, including JSON for policies and schedules.
    - Future story: As an administrator, I want structured modals/forms with validation so dashboard edits are less error-prone.

26. Incident detail deep links.
    - Gap: The dashboard has a selected incident panel but no durable incident URL.
    - Future story: As a responder, I want shareable incident links so teammates can open the exact incident context.

27. Role-aware dashboard navigation.
    - Gap: API enforces roles, but the dashboard can further tailor visible actions by role.
    - Future story: As a stakeholder or responder, I want the dashboard to hide unavailable actions so I understand what I can do without hitting authorization errors.

28. Bulk operations.
    - Gap: Management actions are mostly one item at a time.
    - Future story: As an administrator, I want bulk updates for users, schedules, and incidents so routine maintenance is faster.

## Suggested Planning Priorities

1. Production foundation: Alembic migrations, outbound call cancellation, observability, and deployment documentation.
2. Operator safety: audit log, notification attempt UI, incident filtering, and structured dashboard forms.
3. Scheduling depth: guided policy/schedule editors, escalation simulation, and override lifecycle management.
4. Integrations: Slack/Teams/webhooks, notification templates, and secure local recording playback.
5. Compliance and scale: retention controls, session auth, stronger credential lifecycle, pagination, and reporting.
