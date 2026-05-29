const state = {
  users: [],
  services: [],
  schedules: [],
  policies: [],
  incidents: [],
  scheduleGaps: {},
  selectedIncidentId: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function shortId(id) {
  return id ? id.slice(0, 8) : "none";
}

function serviceName(id) {
  return state.services.find((service) => service.id === id)?.name ?? shortId(id);
}

function userName(id) {
  return state.users.find((user) => user.id === id)?.name ?? (id ? shortId(id) : "Unassigned");
}

function policyName(id) {
  return state.policies.find((policy) => policy.id === id)?.name ?? shortId(id);
}

function responders() {
  return state.users.filter((user) => user.role !== "stakeholder");
}

function channelValue(user) {
  return (user.notification_preferences?.channels || ["phone_call", "sms", "email"]).join(",");
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.style.background = isError ? "#8a1f17" : "#101820";
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 3600);
}

async function api(path, options = {}) {
  const url = new URL(path, window.location.origin).toString();
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(Array.isArray(detail) ? JSON.stringify(detail) : detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

async function refreshData() {
  const [users, services, schedules, policies, incidents] = await Promise.all([
    api("/users"),
    api("/services"),
    api("/schedules"),
    api("/escalation-policies"),
    api("/incidents"),
  ]);
  Object.assign(state, { users, services, schedules, policies, incidents });
  if (!incidents.some((incident) => incident.id === state.selectedIncidentId)) {
    state.selectedIncidentId = incidents[0]?.id ?? null;
  }
  renderAll();
  $("#last-refresh").textContent = `Last refreshed ${new Date().toLocaleTimeString()}`;
}

function renderAll() {
  renderSelects();
  renderOverview();
  renderIncidents();
  renderUsers();
  renderServices();
  renderSchedules();
  renderPolicies();
}

function renderSelects() {
  const sources = {
    users: state.users.map((user) => [user.id, `${user.name} (${user.role})`]),
    responders: responders().map((user) => [user.id, `${user.name} (${user.role})`]),
    stakeholders: state.users.filter((user) => user.role === "stakeholder").map((user) => [user.id, user.name]),
    services: state.services.map((service) => [service.id, service.name]),
    schedules: state.schedules.map((schedule) => [schedule.id, schedule.name]),
    policies: state.policies.map((policy) => [policy.id, policy.name]),
  };
  $$("select[data-source]").forEach((select) => {
    const current = select.value;
    const values = sources[select.dataset.source] || [];
    const optional = !select.required;
    select.innerHTML = [
      optional ? '<option value="">None</option>' : '<option value="">Select</option>',
      ...values.map(([id, label]) => `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`),
    ].join("");
    if (values.some(([id]) => id === current)) select.value = current;
  });
}

function renderOverview() {
  const triggered = state.incidents.filter((incident) => incident.status === "triggered").length;
  const acknowledged = state.incidents.filter((incident) => incident.status === "acknowledged").length;
  const resolved = state.incidents.filter((incident) => incident.status === "resolved").length;
  $("#metric-grid").innerHTML = [
    ["Triggered", triggered],
    ["Acknowledged", acknowledged],
    ["Resolved", resolved],
    ["Responders", responders().length],
  ]
    .map(([label, value]) => `<div class="metric"><span class="muted">${label}</span><strong>${value}</strong></div>`)
    .join("");

  const openIncidents = state.incidents.filter((incident) => incident.status !== "resolved" && incident.status !== "merged");
  $("#overview-incidents").innerHTML = openIncidents.length
    ? openIncidents
        .slice(0, 6)
        .map(
          (incident) => `
            <tr>
              <td>${escapeHtml(incident.title)}<br><small>${shortId(incident.id)}</small></td>
              <td><span class="status ${incident.status}">${incident.status}</span></td>
              <td><span class="priority ${incident.priority}">${incident.priority}</span></td>
              <td>${escapeHtml(serviceName(incident.service_id))}</td>
              <td>${formatDate(incident.created_at)}</td>
            </tr>`
        )
        .join("")
    : '<tr><td colspan="5" class="empty">No open incidents</td></tr>';

  const health = [
    ["Users", state.users.length, "Create responders and admins before policies page them."],
    ["Services", state.services.length, "Each service maps to one inbound Twilio number."],
    ["Schedules", state.schedules.length, "Schedules can be used as policy targets."],
    ["Policies", state.policies.length, "Services need policies before incidents can escalate."],
  ];
  $("#config-health").innerHTML = health
    .map(
      ([label, count, note]) => `
        <div class="item">
          <div class="item-header"><strong>${label}</strong><span>${count}</span></div>
          <small>${note}</small>
        </div>`
    )
    .join("");
}

function renderIncidents() {
  const filter = $("#incident-filter").value;
  const rows = state.incidents.filter((incident) => {
    if (filter === "all") return true;
    if (filter === "open") return incident.status !== "resolved" && incident.status !== "merged";
    return incident.status === filter;
  });
  $("#incident-table").innerHTML = rows.length
    ? rows
        .map(
          (incident) => `
            <tr>
              <td>${escapeHtml(incident.title)}<br><small>${shortId(incident.id)} - ${escapeHtml(serviceName(incident.service_id))}</small></td>
              <td><span class="status ${incident.status}">${incident.status}</span></td>
              <td><span class="priority ${incident.priority}">${incident.priority}</span></td>
              <td>${escapeHtml(userName(incident.assigned_user_id))}</td>
              <td><button class="secondary-button" data-incident-id="${incident.id}" type="button">Open</button></td>
            </tr>`
        )
        .join("")
    : '<tr><td colspan="5" class="empty">No incidents match this filter</td></tr>';
  $$("[data-incident-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedIncidentId = button.dataset.incidentId;
      renderIncidentDetail();
    });
  });
  renderIncidentDetail();
}

async function renderIncidentDetail() {
  const panel = $("#incident-detail");
  let incident = state.incidents.find((item) => item.id === state.selectedIncidentId);
  if (!incident) {
    panel.innerHTML = '<div class="empty">Select an incident to inspect timeline and responder actions.</div>';
    return;
  }
  try {
    incident = await api(`/incidents/${incident.id}`);
  } catch {
    // Fall back to the list copy so the detail panel still renders during transient refresh failures.
  }
  let timeline = [];
  try {
    timeline = await api(`/incidents/${incident.id}/timeline`);
  } catch {
    timeline = [];
  }
  const responderOptions = responders()
    .map(
      (user) =>
        `<option value="${user.id}" ${user.id === incident.assigned_user_id ? "selected" : ""}>${escapeHtml(user.name)}</option>`
    )
    .join("");
  const allIncidentOptions = state.incidents
    .filter((item) => item.id !== incident.id && item.status !== "merged")
    .map((item) => `<option value="${item.id}">${escapeHtml(item.title)} (${shortId(item.id)})</option>`)
    .join("");
  panel.innerHTML = `
    <div class="panel-heading">
      <h3>${escapeHtml(incident.title)}</h3>
      <span class="status ${incident.status}">${incident.status}</span>
    </div>
    <div class="detail-body">
      <div class="item">
        <div class="item-header"><strong>${escapeHtml(serviceName(incident.service_id))}</strong><span class="priority ${incident.priority}">${incident.priority}</span></div>
        <small>Caller ${escapeHtml(incident.caller_id || "Unknown")} - Created ${formatDate(incident.created_at)} - Assigned ${escapeHtml(userName(incident.assigned_user_id))}</small>
      </div>
      <div class="detail-actions">
        <label class="compact-field">Actor<select id="incident-actor-user" aria-label="Actor">${responderOptions}</select></label>
        <label class="compact-field">Assignee<select id="incident-assignee-user" aria-label="Assignee">${responderOptions}</select></label>
        <button class="secondary-button" data-action="ack" type="button">Acknowledge</button>
        <button class="secondary-button" data-action="resolve" type="button">Resolve</button>
        <button class="danger-button" data-action="reopen" type="button">Reopen</button>
        <button class="secondary-button" data-action="reassign" type="button">Reassign</button>
        <button class="secondary-button" data-action="start" type="button">Start escalation</button>
        <button class="secondary-button" data-action="edit" type="button">Edit</button>
        <button class="secondary-button" data-action="ack-link" type="button">Email ack</button>
        <button class="secondary-button" data-action="resolve-link" type="button">Email resolve</button>
        <button class="danger-button" data-action="merge" type="button">Merge</button>
      </div>
      <div class="merge-picker">
        <label>Child incidents<select id="merge-child-incidents" multiple size="3">${allIncidentOptions}</select></label>
      </div>
      <form id="note-form" class="form-grid">
        <label>Author<select name="author_id">${state.users.map((user) => `<option value="${user.id}">${escapeHtml(user.name)}</option>`).join("")}</select></label>
        <label class="check-row"><input name="status_update" type="checkbox" /> Status update</label>
        <label class="span-2">Note<textarea name="body" rows="3" required></textarea></label>
        <button class="secondary-button" type="submit">Add note</button>
      </form>
      <div>
        <h3>Timeline</h3>
        <div class="timeline">
          ${
            timeline.length
              ? timeline
                  .map(
                    (event) => `
                      <div class="timeline-entry">
                        <strong>${escapeHtml(event.event_type)}</strong>
                        <small>${formatDate(event.occurred_at)} - ${escapeHtml(event.actor)}</small>
                        <div class="mono">${escapeHtml(JSON.stringify(event.payload))}</div>
                      </div>`
                  )
                  .join("")
              : '<div class="empty">No timeline entries</div>'
          }
        </div>
      </div>
    </div>`;

  $$("[data-action]", panel).forEach((button) => {
    button.disabled = responders().length === 0 && button.dataset.action !== "edit";
    button.addEventListener("click", async () => {
      const actorId = $("#incident-actor-user", panel)?.value;
      const assigneeId = $("#incident-assignee-user", panel)?.value;
      const action = button.dataset.action;
      if (action === "ack") {
        await submitJson(`/incidents/${incident.id}/acknowledge`, { user_id: actorId, channel: "dashboard" }, "Incident acknowledged");
      } else if (action === "resolve") {
        await submitJson(`/incidents/${incident.id}/resolve`, { user_id: actorId, channel: "dashboard" }, "Incident resolved");
      } else if (action === "reopen") {
        await submitJson(`/incidents/${incident.id}/reopen`, { user_id: actorId, channel: "dashboard" }, "Incident reopened");
      } else if (action === "reassign") {
        await submitJson(`/incidents/${incident.id}/reassign`, { actor_id: actorId, assignee_id: assigneeId }, "Incident reassigned");
      } else if (action === "start") {
        await mutate("POST", `/incidents/${incident.id}/start-escalation`, undefined, "Escalation started");
      } else if (action === "edit") {
        await editIncident(incident);
      } else if (action === "ack-link") {
        await mutate("GET", `/incidents/${incident.id}/acknowledge-link?user_id=${encodeURIComponent(actorId)}`, undefined, "Email acknowledge action applied");
      } else if (action === "resolve-link") {
        await mutate("GET", `/incidents/${incident.id}/resolve-link?user_id=${encodeURIComponent(actorId)}`, undefined, "Email resolve action applied");
      } else if (action === "merge") {
        await mergeIncident(incident);
      }
    });
  });

  $("#note-form", panel).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.status_update = form.elements.status_update.checked;
    await submitJson(`/incidents/${incident.id}/notes`, payload, "Note added");
    form.reset();
  });
}

function renderUsers() {
  $("#user-table").innerHTML = state.users.length
    ? state.users
        .map(
          (user) => `
            <tr>
              <td>${escapeHtml(user.name)}<br><small>${escapeHtml(user.timezone)}</small></td>
              <td>${escapeHtml(user.role)}</td>
              <td>${escapeHtml(user.email)}</td>
              <td>${escapeHtml(user.phone_number)}</td>
              <td>
                <div class="row-actions">
                  <button class="secondary-button compact-button" data-user-edit="${user.id}" type="button">Edit</button>
                  <button class="danger-button compact-button" data-user-delete="${user.id}" type="button">Delete</button>
                </div>
              </td>
            </tr>`
        )
        .join("")
    : '<tr><td colspan="5" class="empty">No users yet</td></tr>';
  $$("[data-user-edit]").forEach((button) => {
    button.addEventListener("click", () => editUser(state.users.find((user) => user.id === button.dataset.userEdit)));
  });
  $$("[data-user-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteResource(`/users/${button.dataset.userDelete}`, "user"));
  });
}

function renderServices() {
  $("#service-table").innerHTML = state.services.length
    ? state.services
        .map(
          (service) => `
            <tr>
              <td>${escapeHtml(service.name)}<br><small>${escapeHtml(service.description || "")}</small></td>
              <td>${escapeHtml(service.inbound_phone_number)}</td>
              <td>${escapeHtml(policyName(service.escalation_policy_id))}</td>
              <td>
                <div class="row-actions">
                  <button class="secondary-button compact-button" data-service-edit="${service.id}" type="button">Edit</button>
                  <button class="danger-button compact-button" data-service-delete="${service.id}" type="button">Delete</button>
                </div>
              </td>
            </tr>`
        )
        .join("")
    : '<tr><td colspan="4" class="empty">No services yet</td></tr>';
  $$("[data-service-edit]").forEach((button) => {
    button.addEventListener("click", () => editService(state.services.find((service) => service.id === button.dataset.serviceEdit)));
  });
  $$("[data-service-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteResource(`/services/${button.dataset.serviceDelete}`, "service"));
  });
}

function renderSchedules() {
  $("#schedule-list").innerHTML = state.schedules.length
    ? state.schedules
        .map((schedule) => {
          const gaps = state.scheduleGaps[schedule.id];
          return `
            <div class="item">
              <div class="item-header"><strong>${escapeHtml(schedule.name)}</strong><span>${escapeHtml(schedule.timezone)}</span></div>
              <small>${schedule.layers.length} layer(s), ${schedule.overrides.length} override(s)</small>
              <div class="mono">${escapeHtml(schedule.id)}</div>
              ${
                gaps
                  ? `<div class="gap-report">${gaps.length ? gaps.map((gap) => `${escapeHtml(formatDate(gap.start))} to ${escapeHtml(formatDate(gap.end))}`).join("<br>") : "No gaps detected"}</div>`
                  : ""
              }
              <div class="row-actions">
                <button class="secondary-button compact-button" data-schedule-gaps="${schedule.id}" type="button">Gaps</button>
                <button class="secondary-button compact-button" data-schedule-edit="${schedule.id}" type="button">Edit</button>
                <button class="danger-button compact-button" data-schedule-delete="${schedule.id}" type="button">Delete</button>
              </div>
            </div>`;
        })
        .join("")
    : '<div class="empty">No schedules yet</div>';
  $$("[data-schedule-gaps]").forEach((button) => {
    button.addEventListener("click", () => loadScheduleGaps(button.dataset.scheduleGaps));
  });
  $$("[data-schedule-edit]").forEach((button) => {
    button.addEventListener("click", () => editSchedule(state.schedules.find((schedule) => schedule.id === button.dataset.scheduleEdit)));
  });
  $$("[data-schedule-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteResource(`/schedules/${button.dataset.scheduleDelete}`, "schedule"));
  });
}

function renderPolicies() {
  $("#policy-list").innerHTML = state.policies.length
    ? state.policies
        .map(
          (policy) => `
            <div class="item">
              <div class="item-header"><strong>${escapeHtml(policy.name)}</strong><span>${policy.steps.length} step(s)</span></div>
              <small>Repeat ${policy.repeat_enabled ? "enabled" : "disabled"} - Catchall ${escapeHtml(userName(policy.catchall_user_id))}</small>
              <div class="mono">${escapeHtml(JSON.stringify(policy.steps))}</div>
              <div class="row-actions">
                <button class="secondary-button compact-button" data-policy-edit="${policy.id}" type="button">Edit</button>
                <button class="danger-button compact-button" data-policy-delete="${policy.id}" type="button">Delete</button>
              </div>
            </div>`
        )
        .join("")
    : '<div class="empty">No escalation policies yet</div>';
  $$("[data-policy-edit]").forEach((button) => {
    button.addEventListener("click", () => editPolicy(state.policies.find((policy) => policy.id === button.dataset.policyEdit)));
  });
  $$("[data-policy-delete]").forEach((button) => {
    button.addEventListener("click", () => deleteResource(`/escalation-policies/${button.dataset.policyDelete}`, "escalation policy"));
  });
}

function promptText(label, current = "") {
  return window.prompt(label, current ?? "");
}

function promptJson(label, current) {
  const value = window.prompt(label, JSON.stringify(current ?? [], null, 2));
  if (value === null) return null;
  return JSON.parse(value || "[]");
}

function promptBoolean(label, current) {
  const value = window.prompt(label, String(Boolean(current)));
  if (value === null) return null;
  return value.toLowerCase() === "true" || value === "1" || value.toLowerCase() === "yes";
}

function promptNumber(label, current) {
  const value = window.prompt(label, String(current ?? 0));
  if (value === null) return null;
  return Number(value || 0);
}

async function mutate(method, path, payload, success = "Saved") {
  try {
    const options = { method };
    if (payload !== undefined) options.body = JSON.stringify(payload);
    await api(path, options);
    showToast(success);
    await refreshData();
    return true;
  } catch (error) {
    showToast(error.message, true);
    return false;
  }
}

async function submitJson(path, payload, success = "Saved") {
  return mutate("POST", path, payload, success);
}

async function deleteResource(path, label) {
  if (!window.confirm(`Delete this ${label}?`)) return;
  await mutate("DELETE", path, undefined, `${label[0].toUpperCase()}${label.slice(1)} deleted`);
}

function localDateToIso(value) {
  return value ? new Date(value).toISOString() : "";
}

async function editUser(user) {
  if (!user) return;
  try {
    const name = promptText("Name", user.name);
    if (name === null) return;
    const email = promptText("Email", user.email);
    if (email === null) return;
    const phoneNumber = promptText("Phone number", user.phone_number);
    if (phoneNumber === null) return;
    const timezone = promptText("Timezone", user.timezone);
    if (timezone === null) return;
    const role = promptText("Role (responder, admin, stakeholder)", user.role);
    if (role === null) return;
    const channels = promptText("Notification channels, comma separated", channelValue(user));
    if (channels === null) return;
    await mutate(
      "PATCH",
      `/users/${user.id}`,
      {
        name,
        email,
        phone_number: phoneNumber,
        timezone,
        role,
        notification_preferences: { ...(user.notification_preferences || {}), channels: channels.split(",").map((item) => item.trim()).filter(Boolean) },
      },
      "User updated"
    );
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editService(service) {
  if (!service) return;
  const name = promptText("Name", service.name);
  if (name === null) return;
  const inboundPhoneNumber = promptText("Inbound phone number", service.inbound_phone_number);
  if (inboundPhoneNumber === null) return;
  const escalationPolicyId = promptText("Escalation policy ID", service.escalation_policy_id);
  if (escalationPolicyId === null) return;
  const description = promptText("Description", service.description || "");
  if (description === null) return;
  await mutate(
    "PATCH",
    `/services/${service.id}`,
    { name, inbound_phone_number: inboundPhoneNumber, escalation_policy_id: escalationPolicyId, description },
    "Service updated"
  );
}

async function editPolicy(policy) {
  if (!policy) return;
  try {
    const name = promptText("Name", policy.name);
    if (name === null) return;
    const steps = promptJson("Steps JSON", policy.steps);
    if (steps === null) return;
    const repeatEnabled = promptBoolean("Repeat enabled? true or false", policy.repeat_enabled);
    if (repeatEnabled === null) return;
    const repeatCount = promptNumber("Repeat count", policy.repeat_count);
    if (repeatCount === null) return;
    const catchallUserId = promptText("Catchall user ID, blank for none", policy.catchall_user_id || "");
    if (catchallUserId === null) return;
    await mutate(
      "PATCH",
      `/escalation-policies/${policy.id}`,
      {
        name,
        steps,
        repeat_enabled: repeatEnabled,
        repeat_count: repeatCount,
        catchall_user_id: catchallUserId || null,
      },
      "Policy updated"
    );
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editSchedule(schedule) {
  if (!schedule) return;
  try {
    const name = promptText("Name", schedule.name);
    if (name === null) return;
    const timezone = promptText("Timezone", schedule.timezone);
    if (timezone === null) return;
    const layers = promptJson("Layers JSON", schedule.layers);
    if (layers === null) return;
    const overrides = promptJson("Overrides JSON", schedule.overrides);
    if (overrides === null) return;
    await mutate("PATCH", `/schedules/${schedule.id}`, { name, timezone, layers, overrides }, "Schedule updated");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editIncident(incident) {
  const title = promptText("Title", incident.title);
  if (title === null) return;
  const priority = promptText("Priority (P1, P2, P3, P4)", incident.priority);
  if (priority === null) return;
  await mutate("PATCH", `/incidents/${incident.id}`, { title, priority }, "Incident updated");
}

async function mergeIncident(incident) {
  const actorId = $("#incident-actor-user")?.value;
  const selected = $$("#merge-child-incidents option:checked").map((option) => option.value);
  const childIncidentIds =
    selected.length > 0
      ? selected
      : (promptText("Child incident IDs, comma separated", "") || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean);
  if (!childIncidentIds.length) return;
  await submitJson(`/incidents/${incident.id}/merge`, { actor_id: actorId, child_incident_ids: childIncidentIds }, "Incidents merged");
}

async function loadScheduleGaps(scheduleId) {
  try {
    const gaps = await api(`/schedules/${scheduleId}/gaps`);
    state.scheduleGaps[scheduleId] = gaps;
    renderSchedules();
    showToast(gaps.length ? `${gaps.length} gap(s) detected` : "No schedule gaps detected");
  } catch (error) {
    showToast(error.message, true);
  }
}

function wireForms() {
  $("#user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    data.notification_preferences = { channels: data.channels.split(",").map((item) => item.trim()).filter(Boolean) };
    delete data.channels;
    if (await submitJson("/users", data, "User created")) event.currentTarget.reset();
  });

  $("#policy-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = Object.fromEntries(new FormData(event.currentTarget).entries());
      data.steps = JSON.parse(data.steps || "[]");
      data.repeat_enabled = data.repeat_enabled === "true";
      data.repeat_count = Number(data.repeat_count || 0);
      if (!data.catchall_user_id) data.catchall_user_id = null;
      if (await submitJson("/escalation-policies", data, "Policy created")) event.currentTarget.reset();
    } catch (error) {
      showToast(error.message, true);
    }
  });

  $("#service-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    if (await submitJson("/services", data, "Service created")) event.currentTarget.reset();
  });

  $("#stakeholder-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    const action = event.submitter?.dataset.stakeholderAction || "subscribe";
    const method = action === "unsubscribe" ? "DELETE" : "POST";
    const verb = action === "unsubscribe" ? "unsubscribed" : "subscribed";
    await mutate(method, `/services/${data.service_id}/stakeholders/${data.user_id}`, undefined, `Stakeholder ${verb}`);
  });

  $("#schedule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = Object.fromEntries(new FormData(event.currentTarget).entries());
      data.layers = JSON.parse(data.layers || "[]");
      data.overrides = [];
      if (await submitJson("/schedules", data, "Schedule created")) event.currentTarget.reset();
    } catch (error) {
      showToast(error.message, true);
    }
  });

  $("#override-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    const scheduleId = data.schedule_id;
    delete data.schedule_id;
    data.start = localDateToIso(data.start);
    data.end = localDateToIso(data.end);
    if (await submitJson(`/schedules/${scheduleId}/overrides`, data, "Override created")) event.currentTarget.reset();
  });

  $("#incident-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    data.start_escalation = form.elements.start_escalation.checked;
    if (await submitJson("/incidents", data, "Incident created")) form.reset();
  });
}

function wireNavigation() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  $$("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.viewTarget));
  });
  $("#refresh-button").addEventListener("click", () => refreshData().catch((error) => showToast(error.message, true)));
  $("#incident-filter").addEventListener("change", renderIncidents);
}

function showView(view) {
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
}

wireNavigation();
wireForms();
refreshData().catch((error) => showToast(error.message, true));
