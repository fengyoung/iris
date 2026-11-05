"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const STATUS = {queued: "排队中", pending: "等待中", running: "运行中", cancelling: "取消中", success: "成功", succeeded: "成功", completed: "完成", failed: "失败", cancelled: "已取消", canceled: "已取消", interrupted: "中断"};
  const ACTIVE = new Set(["queued", "pending", "running", "cancelling"]);
  const store = {commands: [], groups: [], jobs: [], state: null, command: null, detail: null, offsets: {stdout: 0, stderr: 0}, generation: 0, polling: false, timer: null, catalogBusy: false, searchIndex: 0};
  const token = document.querySelector('meta[name="iris-token"]').content;
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function button(text, fn, cls = "") { const n = el("button", cls, text); n.type = "button"; n.addEventListener("click", fn); return n; }
  function clear(id) { $(id).replaceChildren(); return $(id); }
  function message(id, text) { $(id).textContent = text || ""; $(id).hidden = !text; }
  function badge(status) { return el("span", "badge status-" + (Object.hasOwn(STATUS, status) ? status : "unknown"), STATUS[status] || status || "未知"); }
  function time(value) { if (!value) return "—"; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString("zh-CN", {hour12: false}); }
  function duration(start, end) { const seconds = Math.max(0, Math.floor(((end ? Date.parse(end) : Date.now()) - Date.parse(start)) / 1000)); if (!Number.isFinite(seconds)) return "—"; return seconds >= 3600 ? `${Math.floor(seconds / 3600)}小时${Math.floor(seconds % 3600 / 60)}分` : seconds >= 60 ? `${Math.floor(seconds / 60)}分${seconds % 60}秒` : `${seconds}秒`; }
  function idOf(job) { return job.id || job.job_id; }
  function quote(value) { const s = String(value); return /^[a-zA-Z0-9_@%+=:,./-]+$/.test(s) ? s : "'" + s.replaceAll("'", "'\"'\"'") + "'"; }
  async function api(path, data) {
    const options = {cache: "no-store", signal: AbortSignal.timeout(15000)};
    if (data !== undefined) { options.method = "POST"; options.headers = {"Content-Type": "application/json", "X-Iris-Token": token}; options.body = JSON.stringify(data); }
    const response = await fetch(path, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.error === "string" ? body.error : body.error?.message || body.message || `请求失败（HTTP ${response.status}）`);
    return body;
  }
  function view(name) {
    const views = {overview: ["今日总览", "本地知识的状态、待办与下一步。"], wizards: ["操作向导", "按步骤完成工作，执行过程始终可追踪。"], tasks: ["任务中心", "启动、跟踪与回看你的知识工作。"], catalog: ["命令目录", "找到合适的工具，让本地知识开始工作。"]};
    if (!Object.hasOwn(views, name)) name = "overview";
    for (const key of Object.keys(views)) $("view-" + key).hidden = key !== name;
    document.querySelectorAll("[data-view]").forEach((n) => { n.classList.toggle("active", n.dataset.view === name); if (n.dataset.view === name) n.setAttribute("aria-current", "page"); else n.removeAttribute("aria-current"); });
    $("page-title").textContent = views[name][0];
    $("page-description").textContent = views[name][1];
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  }
  function renderState(data) {
    store.state = data;
    $("status-dot").className = "dot"; $("status-text").textContent = "本地服务已连接"; $("offline-banner").hidden = true;
    const daemon = data.daemon || {};
    $("daemon-info").textContent = `守护进程 PID ${daemon.pid || "—"} · 端口 ${daemon.port || "—"} · 已运行 ${Math.floor((daemon.uptime_sec || 0) / 60)} 分钟 · v${data.version || "—"}`;
    const running = data.running || [], historyItems = data.history || [];
    const summary = clear("summary");
    for (const [label, value, hint] of [["CLI 运行中", running.length, "CLI 与 Agent 上报"], ["历史成功", historyItems.filter(t => t.status === "success").length, "最近记录"], ["失败 / 中断", historyItems.filter(t => ["failed", "interrupted"].includes(t.status)).length, "可在下方查看原因"], ["控制台任务", store.jobs.length, "独立进程与日志"]]) { const item = el("div", "stat"); item.append(el("span", "muted", label), el("strong", "", value), el("small", "muted", hint)); summary.append(item); }
    const filter = $("agent-filter"), saved = filter.value;
    const agents = [...new Set([...(data.agents || []), ...running.map(t => t.agent_id), ...historyItems.map(t => t.agent_id)].filter(Boolean))];
    filter.replaceChildren(); const all = el("option", "", "全部"); all.value = "__all__"; filter.append(all);
    agents.forEach(a => { const option = el("option", "", a); option.value = a; filter.append(option); }); filter.value = agents.includes(saved) ? saved : "__all__";
    const watchdog = clear("watchdog-row");
    (data.watchdog || []).forEach(w => { const item = el("span", "watchdog"); item.append(el("span", w.status === "running" ? "dot" : "dot muted"), el("span", "", `${w.name} · ${w.status === "running" ? "运行中 · PID " + w.pid : "未运行"}`)); watchdog.append(item); });
    renderLegacy();
    if (data.interrupted_now?.length) message("notice", `检测到 ${data.interrupted_now.length} 个进程中断，已归档到任务历史。`);
  }
  function renderLegacy() {
    if (!store.state) return;
    const filter = $("agent-filter").value;
    const visible = t => filter === "__all__" || t.agent_id === filter;
    const running = (store.state.running || []).filter(visible), historyItems = (store.state.history || []).filter(visible).slice().reverse();
    const grid = clear("running-grid");
    if (!running.length) grid.append(el("p", "empty", "当前没有 CLI 或 Agent 任务运行。长任务上报后会自动出现在这里。"));
    running.forEach(t => { const card = el("article", "task-card"), top = el("div", "card-top"); top.append(el("h4", "", t.name), badge("running")); card.append(top, el("p", "phase", `${t.phase || "init"} · ${t.phase_detail || "启动中…"}`)); if (Number.isFinite(t.progress)) { const p = el("progress"); p.max = 1; p.value = Math.max(0, Math.min(1, t.progress)); p.setAttribute("aria-label", "任务进度"); card.append(p); } card.append(el("p", "muted", `${t.agent_id || "default"} · PID ${t.pid || "—"} · 耗时 ${duration(t.started_at)} · 启动 ${time(t.started_at)}`)); if (t.command) card.append(el("code", "command-line", t.command)); grid.append(card); });
    const list = clear("history-list"); if (!historyItems.length) list.append(el("p", "empty", "暂无历史记录。任务结束后会自动归档。"));
    historyItems.forEach(t => { const row = el("article", "history-row"); const info = el("div", "grow"); info.append(el("strong", "", t.name), el("small", "muted", `${t.agent_id || "default"} · ${time(t.started_at)} → ${time(t.ended_at)} · ${duration(t.started_at, t.ended_at)}`)); if (t.error) info.append(el("p", "error-text", t.error)); row.append(badge(t.status), info); list.append(row); });
  }
  function renderJobs() {
    const list = clear("jobs-list");
    if (!store.jobs.length) { const empty = el("div", "empty-state"); empty.append(el("h3", "", "开始一项知识工作"), el("p", "muted", "从命令目录启动任务，实时查看状态、标准输出与错误输出。"), button("浏览命令目录 →", () => view("catalog"))); list.append(empty); return; }
    store.jobs.slice().sort((a, b) => String(b.created_at || b.started_at || "").localeCompare(String(a.created_at || a.started_at || ""))).forEach(job => { const row = el("article", "job-row"); const info = el("div", "grow"); info.append(button(job.command || job.name || idOf(job), () => openJob(job), "text-button job-name"), el("small", "muted", `${time(job.created_at || job.started_at)} · 耗时 ${duration(job.started_at, job.ended_at)} · ${idOf(job)}`)); if (job.error) info.append(el("p", "error-text", job.error)); const actions = el("div", "row-actions"); actions.append(button("详情", () => openJob(job))); if (ACTIVE.has(job.status)) { const cancel = button("取消", () => jobAction(job, "cancel", cancel)); cancel.disabled = job.status === "cancelling" || job.can_cancel === false; if (job.can_cancel === false) cancel.title = "此进程不由当前服务持有，无法取消"; actions.append(cancel); } else { const rerun = button("重跑", () => jobAction(job, "rerun", rerun)); actions.append(rerun); } row.append(badge(job.status), info, actions); list.append(row); });
  }
  async function jobAction(job, action, source) {
    const spec = store.commands.find(c => c.name === job.command);
    if (action === "rerun" && !spec) { message("notice", "命令目录尚未就绪，正在重新加载。请稍后重试，以确认此命令的运行要求。"); loadCatalog(); return; }
    if (action === "rerun" && spec?.blocked) { message("notice", spec.blocked); return; }
    if (action === "rerun" && spec?.confirm && !window.confirm(`确认重新执行 ${job.command}？\n${spec.desc}\n将使用原任务参数。`)) return;
    if (action === "cancel" && !window.confirm(`确认取消 ${job.command || idOf(job)}？正在运行的进程将被终止。`)) return;
    source.disabled = true;
    try { const result = await api(`/api/jobs/${encodeURIComponent(idOf(job))}/${action}`, {}); const updated = result.job || result; message("notice", action === "cancel" ? "取消请求已提交。" : "已创建重跑任务。"); if (action === "rerun") openJob(updated); await refreshJobs(); }
    catch (error) { message("notice", error.message); $("job-error").textContent = error.message; }
    finally { source.disabled = false; }
  }
  function renderDetail(job) {
    $("job-title").textContent = job.command || job.name || idOf(job);
    const meta = clear("job-meta"); meta.append(badge(job.status), el("span", "", `ID ${idOf(job)}`), el("span", "", `PID ${job.pid || "—"}`), el("span", "", `退出码 ${job.returncode ?? job.exit_code ?? "—"}`), el("span", "", `创建 ${time(job.created_at)}`), el("span", "", `开始 ${time(job.started_at)} · 结束 ${time(job.ended_at)}`));
    const cli = job.cli || job.command_line || (Array.isArray(job.argv) ? job.argv.map(quote).join(" ") : "");
    $("job-cli").textContent = cli || JSON.stringify(job.params || {}, null, 2);
    $("job-error").textContent = job.error || "";
    const actions = clear("job-actions"); const action = ACTIVE.has(job.status) ? "cancel" : "rerun";
    const b = button(action === "cancel" ? "取消任务" : "使用原参数重跑", () => jobAction(job, action, b)); b.disabled = job.status === "cancelling" || (action === "cancel" && job.can_cancel === false); if (action === "cancel" && job.can_cancel === false) b.title = "此进程不由当前服务持有，无法取消"; actions.append(b);
  }
  function openJob(job) {
    store.detail = idOf(job); store.generation += 1; store.offsets = {stdout: 0, stderr: 0};
    clear("stdout-log"); clear("stderr-log"); renderDetail(job);
    if (!$("job-dialog").open) $("job-dialog").showModal();
    refreshDetail();
  }
  let detailBusy = false;
  async function refreshDetail() {
    if (!store.detail || !$("job-dialog").open || detailBusy) return;
    const id = store.detail, generation = store.generation; detailBusy = true;
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (generation !== store.generation) return;
      renderDetail(job.job || job);
      await Promise.all(["stdout", "stderr"].map(async stream => {
        const data = await api(`/api/jobs/${encodeURIComponent(id)}/logs?stream=${stream}&offset=${store.offsets[stream]}`);
        if (generation !== store.generation) return;
        const output = $(stream + "-log");
        if (Number.isFinite(data.offset) && data.offset < store.offsets[stream]) output.textContent = "";
        output.append(document.createTextNode(data.text || ""));
        // Keep browser memory bounded; offsets always refer to server bytes, never JS string lengths.
        if (output.textContent.length > 1000000) output.textContent = "[较早输出已从页面省略]\n" + output.textContent.slice(-800000);
        if (Number.isFinite(data.next_offset)) store.offsets[stream] = data.next_offset;
        if ($("log-follow").checked) output.scrollTop = output.scrollHeight;
      }));
    } catch (error) { if (generation === store.generation) $("job-error").textContent = `详情刷新失败：${error.message}`; }
    finally { detailBusy = false; }
  }
  function matches(command, query) { return `${command.name} ${command.desc} ${command.group_label}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()); }
  function renderCatalog() {
    const query = $("catalog-search").value, group = $("group-filter").value;
    const commands = store.commands.filter(c => (!group || c.group === group) && matches(c, query));
    $("catalog-count").textContent = `${commands.length} 个命令`;
    const grid = clear("catalog-grid"); if (!commands.length) grid.append(el("p", "empty", "没有匹配的命令。试试其他关键词或分组。"));
    commands.forEach(c => { const card = el("article", "command-card"), top = el("div", "card-top"); top.append(el("span", "group-tag", c.group_label || c.group), el("span", "muted", c.blocked ? "终端运行" : c.readonly ? "只读" : c.heavy ? "长任务" : "可执行")); card.append(top, el("h3", "", c.name), el("p", "", c.desc)); if (c.blocked) card.append(el("p", "blocked-reason", c.blocked)); const b = button(c.blocked ? "查看命令" : "配置并运行 →", () => openCommand(c), "command-open"); card.append(b); grid.append(card); });
  }
  async function loadCatalog() {
    if (store.catalogBusy) return; store.catalogBusy = true;
    try { const data = await api("/api/catalog"); store.commands = data.commands || []; store.groups = data.groups || []; const select = $("group-filter"), saved = select.value; select.replaceChildren(); const all = el("option", "", "全部分组"); all.value = ""; select.append(all); store.groups.forEach(g => { const o = el("option", "", g.label); o.value = g.id; select.append(o); }); select.value = saved; message("catalog-error", ""); renderCatalog(); }
    catch (error) { message("catalog-error", `命令目录加载失败：${error.message}。点击右上角刷新重试。`); }
    finally { store.catalogBusy = false; }
  }
  function selectedAction() { return store.command?.actions?.find(a => a.value === $("param-action")?.value); }
  function visibleArgs() { const action = selectedAction(); return (store.command?.args || []).filter(a => !action || a.dest === "workspace" || action.args.includes(a.dest)); }
  function field(spec) {
    const wrapper = el("div", "field"), label = el("label", "", spec.label || spec.flag || spec.dest); label.htmlFor = "param-" + spec.dest;
    let input;
    if (spec.widget === "select") { input = el("select"); const empty = el("option", "", "使用默认值"); empty.value = ""; input.append(empty); (spec.choices || []).forEach(value => { const o = el("option", "", value); o.value = String(value); input.append(o); }); }
    else { input = el("input"); input.type = ["checkbox", "number"].includes(spec.widget) ? spec.widget : "text"; if (spec.widget === "number") input.step = spec.step ?? (spec.number_type === "int" ? 1 : "any"); }
    input.id = "param-" + spec.dest; input.name = spec.dest;
    if (spec.widget === "checkbox") { input.checked = Boolean(spec.default); wrapper.classList.add("checkbox-field"); }
    else if (spec.default !== undefined && spec.default !== null) input.value = String(spec.default);
    if (spec.required) input.required = true;
    wrapper.append(label, input); if (spec.help) wrapper.append(el("small", "muted", spec.help)); return wrapper;
  }
  function actionFields() {
    const box = $("argument-fields"); box.replaceChildren(); const action = selectedAction();
    if (action?.subject) box.append(field({dest: "subject", label: "目标（编号或搜索词）", widget: "text", required: true}));
    visibleArgs().forEach(a => box.append(field(a))); updatePreview();
  }
  function openCommand(command) {
    store.command = command; $("command-title").textContent = command.name; $("command-desc").textContent = command.desc; $("form-error").textContent = "";
    message("command-warning", command.blocked || (command.confirm ? "此命令涉及重要修改，启动前需要确认。" : command.heavy ? "此命令可能运行较长时间，可在任务中心跟踪进度。" : ""));
    $("command-submit").disabled = Boolean(command.blocked); $("command-submit").textContent = command.blocked ? "请在终端运行" : "启动任务";
    const fields = clear("command-fields");
    if (command.actions?.length) { fields.append(field({dest: "action", label: "子操作", widget: "select", choices: command.actions.map(a => a.value), default: command.actions[0].value})); const select = $("param-action"); select.replaceChildren(...command.actions.map(a => { const o = el("option", "", a.label); o.value = a.value; return o; })); select.addEventListener("change", actionFields); }
    const args = el("div", "form-fields"); args.id = "argument-fields"; fields.append(args); actionFields();
    if (!$("command-dialog").open) $("command-dialog").showModal();
  }
  function params() {
    const result = {}, action = selectedAction();
    if (action) { result.action = action.value; if (action.subject) result.subject = $("param-subject").value.trim(); }
    visibleArgs().forEach(a => { const input = $("param-" + a.dest); if (a.widget === "checkbox") result[a.dest] = input.checked; else if (input.value !== "") result[a.dest] = a.widget === "number" ? Number(input.value) : input.value; }); return result;
  }
  function equivalentCLI(command, values) {
    const argv = [command.name]; if (values.action) argv.push(values.action); if (values.subject) argv.push(values.subject);
    for (const a of command.args || []) { const value = values[a.dest]; if (value === undefined || value === null || value === "") continue; if (a.widget === "checkbox") { if (value) argv.push(a.flag); } else if ((a.widget === "number" && value === a.default) || (a.widget === "select" && String(value) === String(a.default))) continue; else argv.push(`${a.flag}=${value}`); }
    return "python3 scripts/run_cli.py " + argv.map(quote).join(" ");
  }
  function updatePreview() { if (store.command) $("cli-preview").textContent = equivalentCLI(store.command, params()); }
  $("command-form").addEventListener("input", updatePreview);
  $("command-form").addEventListener("change", updatePreview);
  $("command-form").addEventListener("submit", async event => {
    event.preventDefault(); const command = store.command; if (!command || command.blocked || $("command-submit").disabled) return;
    const values = params(); if (command.confirm && !window.confirm(`确认执行 ${command.name}？\n${command.desc}\n\n${equivalentCLI(command, values)}`)) return;
    $("command-submit").disabled = true; $("command-submit").textContent = "正在启动…"; $("form-error").textContent = "";
    try { const result = await api("/api/jobs", {command: command.name, params: values}); $("command-dialog").close(); view("tasks"); openJob(result.job || result); await refreshJobs(); }
    catch (error) { $("form-error").textContent = error.message; }
    finally { $("command-submit").disabled = Boolean(store.command?.blocked); $("command-submit").textContent = store.command?.blocked ? "请在终端运行" : "启动任务"; }
  });
  function renderSearch() {
    const results = clear("search-results"), commands = store.commands.filter(c => matches(c, $("quick-search").value)).slice(0, 30); store.searchIndex = 0;
    if (!commands.length) results.append(el("p", "empty", store.commands.length ? "没有匹配的命令" : "目录尚未加载，请关闭后刷新重试。"));
    commands.forEach(c => { const b = button("", () => { $("search-dialog").close(); openCommand(c); }, "search-result"); b.append(el("strong", "", c.name), el("span", "muted", c.desc), el("small", "", c.group_label)); results.append(b); }); highlightSearch();
  }
  function highlightSearch() { const rows = $("search-results").querySelectorAll("button"); rows.forEach((n, i) => n.classList.toggle("selected", i === store.searchIndex)); const current = rows[store.searchIndex]; if (current) current.scrollIntoView({block: "nearest"}); }
  function openSearch() { if ($("command-dialog").open || $("job-dialog").open) return; if (!$("search-dialog").open) $("search-dialog").showModal(); $("quick-search").value = ""; renderSearch(); $("quick-search").focus(); }
  $("quick-search").addEventListener("input", renderSearch);
  $("quick-search").addEventListener("keydown", event => { const rows = $("search-results").querySelectorAll("button"); if (!rows.length) return; if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); store.searchIndex = (store.searchIndex + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length; highlightSearch(); } else if (event.key === "Enter") { event.preventDefault(); rows[store.searchIndex].click(); } });
  async function refreshJobs() { try { const data = await api("/api/jobs"); store.jobs = data.jobs || []; renderJobs(); const count = $("summary").querySelectorAll(".stat strong")[3]; if (count) count.textContent = store.jobs.length; message("jobs-error", ""); } catch (error) { message("jobs-error", `控制台任务读取失败：${error.message}（保留上次数据）`); } }
  async function poll() {
    if (store.polling || document.hidden) return; store.polling = true;
    try { await Promise.all([api("/api/state").then(renderState).catch(() => { $("status-dot").className = "dot muted"; $("status-text").textContent = "状态连接失败"; $("offline-banner").hidden = false; }), refreshJobs(), refreshDetail(), refreshWizardJobs(), refreshOverview()]); }
    finally { store.polling = false; }
  }
  document.querySelectorAll("[data-view]").forEach(n => n.addEventListener("click", () => view(n.dataset.view)));
  document.querySelectorAll("[data-close]").forEach(n => n.addEventListener("click", () => $(n.dataset.close).close()));
  $("job-dialog").addEventListener("close", () => { store.detail = null; store.generation += 1; });
  $("new-job").addEventListener("click", () => { view("catalog"); $("catalog-search").focus(); });
  $("search-open").addEventListener("click", openSearch);
  $("refresh").addEventListener("click", () => { loadCatalog(); poll(); });
  $("agent-filter").addEventListener("change", renderLegacy);
  $("catalog-search").addEventListener("input", renderCatalog); $("group-filter").addEventListener("change", renderCatalog);
  document.addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); } });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { clearInterval(store.timer); store.timer = null; } else { poll(); if (!store.timer) store.timer = setInterval(poll, 2000); } });
  window.addEventListener("hashchange", () => view(location.hash.slice(1)));
  const wizardNames = {meeting: "会议处理", feishu: "飞书文档导入", wiki: "Wiki 构建", memory: "记忆维护", feed: "Feed 审核"};
  let currentWizard = "meeting", overviewBusy = false, overviewAt = 0;
  const wizardState = Object.fromEntries(Object.keys(wizardNames).map(key => [key, {data: null, error: "", busy: false, steps: {}, url: "", selected: [], review: null}]));
  // Session storage keeps workflow associations across reloads; the server revalidates each transition.
  try { const saved = JSON.parse(sessionStorage.getItem("iris-wizards") || "{}"); for (const key of Object.keys(wizardState)) if (saved[key]) Object.assign(wizardState[key], saved[key], {busy: false, data: null, error: ""}); } catch (_) { /* Storage is optional. */ }
  function saveWizards() { try { sessionStorage.setItem("iris-wizards", JSON.stringify(wizardState)); } catch (_) { /* Storage is optional. */ } }
  function textRow(title, detail) { const row = el("div", "overview-row"); row.append(el("strong", "", title)); if (detail) row.append(el("p", "muted", detail)); return row; }
  function launchNamed(name) { const command = store.commands.find(c => c.name === name); if (command) openCommand(command); else message("notice", "命令目录尚未加载，请刷新后重试。"); }
  function overviewTask(row) { const n = textRow(row.name || row.command, `${STATUS[row.status] || row.status} · ${time(row.started_at)}`); if (row.job_id) n.append(button("查看任务", async () => { try { openJob(await api(`/api/jobs/${encodeURIComponent(row.job_id)}`)); } catch (e) { message("notice", e.message); } })); return n; }
  async function refreshOverview(force = false) {
    if (overviewBusy || (!force && Date.now() - overviewAt < 15000)) return; overviewBusy = true;
    try { const data = await api("/api/overview"); const grid = clear("overview-grid");
      const section = (key, title, render) => { const card = el("article", "overview-card"); card.dataset.section = key; card.append(el("h2", "", title)); const part = data[key]; if (part?.status !== "ok") { card.append(el("p", "error-text", `此分区暂不可用：${part?.error || "未返回数据"}`), button("重试", () => refreshOverview(true))); } else { try { render(card, part.data); } catch (_) { card.append(el("p", "error-text", "数据格式暂不支持，请刷新后重试。")); } } grid.append(card); };
      section("tasks", "任务与每日维护", (card, d) => { card.append(textRow(`${d.running_count} 项任务运行中`, "CLI、Agent 与控制台任务")); if (d.latest_daily_start) card.append(overviewTask(d.latest_daily_start)); else card.append(el("p", "muted", "尚无每日维护记录")); card.append(button("每日维护", () => launchNamed("daily-start")), button("任务中心", () => view("tasks"))); (d.failed || []).forEach(row => card.append(overviewTask(row))); });
      section("usage", "模型用量", (card, d) => { for (const [key, label] of [["today", "今日"], ["week", "本周"]]) card.append(textRow(`${label} · ${d[key].calls} 次调用`, `${Number(d[key].total_tokens).toLocaleString()} tokens · 输入 ${Number(d[key].prompt_tokens).toLocaleString()} / 输出 ${Number(d[key].completion_tokens).toLocaleString()}`)); card.append(el("small", "muted", `统计时区 ${d.timezone} · 本周自 ${d.week.since}`)); });
      section("freshness", "数据新鲜度", (card, d) => { card.append(el("p", "", d.suggested_next_action)); (d.source_statuses || []).forEach(row => card.append(textRow(row.source_name, `${!row.scan_exists ? "未扫描" : row.scan_stale ? "扫描待更新" : "扫描已就绪"} · ${!row.chunk_exists ? "未建立索引" : row.index_stale ? "索引待更新" : "索引已就绪"}`))); });
      section("reminders", "主动提醒", (card, d) => { card.append(textRow(`${d.signal_count ?? d.signals?.length ?? 0} 条提醒`, d.summary || "")); (d.signals || []).forEach(row => card.append(textRow(row.title || row.target || row.type || "提醒", row.message || row.description || row.reason || row.detail || ""))); });
      section("signals", "最新信号", (card, d) => { if (!d.latest) card.append(el("p", "muted", "尚无信号报告")); else card.append(textRow(d.latest.title, `${time(d.latest.modified_at)}${d.latest.preview ? " · 预览" : ""}`), el("p", "file-path", d.latest.path)); if (d.limitation) card.append(el("p", "muted", d.limitation)); card.append(button("生成信号", () => launchNamed("signals"))); });
      for (const [key, title, target] of [["pending_decisions", "待审核决策", "decisions"], ["pending_feed", "待确认话题", "feed"], ["pending_wiki", "Wiki 候选", "wiki"]]) section(key, title, (card, d) => { card.append(el("p", "muted", `${d.count} 项待处理`)); (d.items || []).forEach(row => card.append(textRow(row.title || row.id, row.path))); card.append(button("前往处理", () => target === "decisions" ? launchNamed("decisions") : chooseWizard(target))); });
      $("overview-time").textContent = `聚合于 ${time(data.generated_at)} · 非任务分区缓存约 30 秒`; overviewAt = Date.now(); message("overview-error", "");
    } catch (e) { message("overview-error", `总览读取失败：${e.message}，已保留上次数据。`); } finally { overviewBusy = false; }
  }
  function chooseWizard(name) { currentWizard = name; view("wizards"); renderWizard(); if (!wizardState[name].data && name !== "feishu") loadWizard(name, name === "wiki" ? wizardState[name].discovery : undefined); }
  async function loadWizard(name, jobId) {
    const w = wizardState[name]; if (w.busy) return; w.busy = true; w.error = ""; renderWizard();
    try { w.data = await api(`/api/wizards/${name}${jobId ? "?job_id=" + encodeURIComponent(jobId) : ""}`); if (name === "wiki" && w.data.job_id) w.discovery = w.data.job_id; }
    catch (e) { w.error = e.message; } finally { w.busy = false; saveWizards(); if (currentWizard === name) renderWizard(); }
  }
  function stepSuccess(w, key) { return w.steps[key]?.status === "success"; }
  function stepRunning(w) { return Object.values(w.steps).some(job => ACTIVE.has(job.status)); }
  async function wizardAction(name, action, payload, confirmText) {
    const w = wizardState[name]; if (w.busy || stepRunning(w)) return;
    if (confirmText && !window.confirm(confirmText)) return;
    w.busy = true; w.error = ""; renderWizard();
    try { const result = await api(`/api/wizards/${action}`, payload);
      if (action === "wiki-select") { w.review = result; delete w.steps["wiki-preview"]; delete w.steps["wiki-write"]; delete w.steps["wiki-lint"]; }
      else { w.steps[action] = result; if (action === "wiki-preview") { delete w.steps["wiki-write"]; delete w.steps["wiki-lint"]; } if (action === "wiki-write") delete w.steps["wiki-lint"]; if (action === "feishu-preview") delete w.steps["feishu-confirm"]; if (action === "wiki-discover") { w.review = null; w.selected = []; w.data = null; delete w.steps["wiki-preview"]; delete w.steps["wiki-write"]; delete w.steps["wiki-lint"]; } }
      saveWizards();
    } catch (e) { w.error = e.message; } finally { w.busy = false; if (currentWizard === name) renderWizard(); }
  }
  async function refreshWizardJobs() {
    await Promise.all(Object.entries(wizardState).map(async ([name, w]) => {
      let changed = false;
      for (const [action, previous] of Object.entries(w.steps)) {
        if (!ACTIVE.has(previous.status)) continue;
        try { const job = await api(`/api/jobs/${encodeURIComponent(idOf(previous))}`); w.steps[action] = job; if (job.status !== previous.status) { changed = true; if (job.status === "success") { if (action === "wiki-discover") await loadWizard("wiki", idOf(job)); else if (["memory", "feed", "meeting"].includes(name)) await loadWizard(name); } } }
        catch (e) { w.error = `任务状态读取失败：${e.message}`; changed = true; }
      }
      if (changed) { saveWizards(); if (currentWizard === name) renderWizard(); }
    }));
  }
  function wizardButton(box, label, name, action, payload, enabled = true, confirmation = "") {
    const w = wizardState[name], b = button(label, () => wizardAction(name, action, typeof payload === "function" ? payload() : payload, confirmation), "wizard-action"); b.dataset.action = action; b.disabled = !enabled || w.busy || stepRunning(w); box.append(b); return b;
  }
  function inputRow(box, id, label, value, onInput, type = "text") { const row = el("label", "wizard-field", label), input = el("input"); input.id = id; input.type = type; input.value = value ?? ""; input.addEventListener("input", () => onInput(input.value)); row.append(input); box.append(row); return input; }
  function renderWizard() {
    const name = currentWizard, w = wizardState[name], tabs = clear("wizard-tabs");
    Object.entries(wizardNames).forEach(([key, label]) => { const b = button(label, () => chooseWizard(key), key === name ? "active" : ""); b.dataset.wizard = key; tabs.append(b); });
    const box = clear("wizard-body"), top = el("div", "section-heading"); top.append(el("h2", "", wizardNames[name])); if (name !== "feishu") { const reload = button(w.busy ? "正在读取…" : "刷新列表 / 重试", () => loadWizard(name, name === "wiki" ? w.discovery : undefined)); reload.disabled = w.busy; top.append(reload); } box.append(top);
    if (w.error) box.append(el("p", "notice error", w.error));
    const description = {meeting: "选择本地会议文件，生成转写或纪要。", feishu: "先预览文档处理结果，确认后再导入。修改 URL 后必须重新预览。", wiki: "发现候选 → 选择审核 → 预览 → 写入 → 质量检查。每一步成功后再继续。", memory: "人工确认纠正规则、调整置顶状态或删除。", feed: "审核待确认话题，选择导入或忽略。"};
    box.append(el("p", "muted", description[name]));
    if (name === "feishu") {
      const url = inputRow(box, "wizard-feishu-url", "飞书文档 URL", w.url, value => { w.url = value; w.steps = {}; w.error = ""; saveWizards(); $("feishu-confirm-button").disabled = true; $("wizard-body").querySelector(".wizard-steps")?.remove(); }); url.type = "url"; url.disabled = w.busy || stepRunning(w);
      wizardButton(box, "1. 预览文档", name, "feishu-preview", () => ({url: w.url.trim()}));
      const confirm = wizardButton(box, "2. 确认导入", name, "feishu-confirm", () => ({url: w.url.trim(), preview_job_id: idOf(w.steps["feishu-preview"])}), stepSuccess(w, "feishu-preview"), "确认导入此飞书文档到本地？"); confirm.id = "feishu-confirm-button";
    } else if (name === "wiki") {
      const limit = inputRow(box, "wizard-wiki-limit", "发现数量上限", w.limit ?? 20, value => { w.limit = value; saveWizards(); }, "number"); limit.min = "1"; limit.step = "1";
      wizardButton(box, "1. 发现候选", name, "wiki-discover", () => ({limit: Number(w.limit ?? 20), incremental: true}));
      if (w.data?.jobs?.length) { const label = el("label", "wizard-field", "恢复已完成的发现任务"), select = el("select"); select.id = "wizard-wiki-history"; select.disabled = w.busy || stepRunning(w); w.data.jobs.forEach(job => { const option = el("option", "", `${time(job.started_at)} · ${job.id.slice(0, 8)}`); option.value = job.id; select.append(option); }); select.value = w.discovery || w.data.job_id; select.addEventListener("change", () => { w.selected = []; w.review = null; w.steps = {}; loadWizard("wiki", select.value); }); label.append(select); box.append(label); }
      const items = w.data?.items || []; const candidates = el("div", "wizard-items");
      items.forEach(item => { const row = el("label", "candidate-row"), check = el("input"); check.type = "checkbox"; check.checked = w.selected.includes(item.index); check.disabled = w.busy || stepRunning(w); check.addEventListener("change", () => { w.selected = check.checked ? [...w.selected, item.index] : w.selected.filter(i => i !== item.index); w.review = null; delete w.steps["wiki-preview"]; delete w.steps["wiki-write"]; delete w.steps["wiki-lint"]; saveWizards(); renderWizard(); }); const detail = textRow(item.title, `${item.page_type} · ${item.query}`); row.append(check, detail); candidates.append(row); }); box.append(candidates);
      if (w.data && !items.length) box.append(el("p", "empty", "此发现任务没有可选候选。可以重新发现。"));
      wizardButton(box, `2. 确认选择（${w.selected.length}）`, name, "wiki-select", () => ({job_id: w.discovery, selected: w.selected}), Boolean(w.discovery && w.selected.length));
      if (w.review) box.append(el("p", "notice", `已保存 ${w.review.items.length} 项审核选择：${w.review.items.map(i => i.title).join("、")}`));
      wizardButton(box, "3. 预览构建", name, "wiki-preview", () => ({review_id: w.review.review_id}), Boolean(w.review));
      wizardButton(box, "4. 写入 Wiki", name, "wiki-write", () => ({review_id: w.review.review_id, preview_job_id: idOf(w.steps["wiki-preview"])}), Boolean(w.review) && stepSuccess(w, "wiki-preview"), "确认按已审核的候选写入 Wiki？请先查看预览任务输出。");
      wizardButton(box, "5. 质量检查", name, "wiki-lint", {}, stepSuccess(w, "wiki-write"));
    } else {
      const items = w.data?.items || []; if (!w.data && !w.error) box.append(el("p", "empty", "正在读取本地列表…")); else if (!items.length && w.data) box.append(el("p", "empty", name === "meeting" ? "未找到会议文件，请检查会议转写目录配置。" : "当前没有待处理条目。"));
      if (name === "meeting") { const label = el("label", "wizard-check"), check = el("input"); check.type = "checkbox"; check.id = "meeting-to-source"; check.checked = Boolean(w.toSource); check.addEventListener("change", () => { w.toSource = check.checked; saveWizards(); }); label.append(check, document.createTextNode(" 将结果归档到 SOURCE")); box.append(label); }
      items.forEach(item => { const row = el("article", "wizard-item");
        if (name === "meeting") { row.append(textRow(item.name, `${item.kind === "audio" ? "音频 / 视频" : "转写文本"} · ${time(item.modified_at * 1000)}`), el("small", "file-path", item.path)); wizardButton(row, "处理此会议", name, "meeting-transcribe", () => ({file_id: item.id, to_source: Boolean(w.toSource)})); }
        if (name === "feed") { row.append(textRow(item.title || item.topic_id, item.summary || item.description || "")); wizardButton(row, "确认导入", name, "feed-confirm", {topic_id: String(item.topic_id)}); wizardButton(row, "忽略", name, "feed-ignore", {topic_id: String(item.topic_id)}, true, "确认忽略此话题？"); }
        if (name === "memory") { row.append(textRow(item.concept, item.preferred), el("small", "muted", `${item.pinned ? "已置顶 · " : ""}更新 ${item.update_count} 次 · ${time(item.updated_at)}`)); wizardButton(row, "确认规则", name, "memory-confirm", {concept: item.concept}); wizardButton(row, item.pinned ? "取消置顶" : "置顶", name, "memory-pin", {concept: item.concept, unpin: Boolean(item.pinned)}); wizardButton(row, "删除", name, "memory-delete", {concept: item.concept}, true, `确认删除纠正规则「${item.concept}」？`); }
        box.append(row);
      });
    }
    if (Object.keys(w.steps).length) { const historyBox = el("div", "wizard-steps"); historyBox.append(el("h3", "", "执行记录 · 关闭详情不会中断向导")); for (const [action, job] of Object.entries(w.steps)) { const row = el("div", "wizard-step"); row.append(el("span", "", action), badge(job.status), button("查看输出", () => openJob(job))); if (job.error) row.append(el("p", "error-text", job.error)); if (!ACTIVE.has(job.status) && job.status !== "success") row.append(el("p", "muted", "本步未成功，请查看输出并使用上方对应操作重试。")); historyBox.append(row); } box.append(historyBox); }
  }
  $("overview-refresh").addEventListener("click", () => refreshOverview(true));
  renderWizard(); loadWizard("meeting"); refreshOverview(true);
  window.IrisConsole = Object.freeze({refresh: poll, openCommand: name => { const command = store.commands.find(c => c.name === name); if (command) openCommand(command); }});
  view(location.hash.slice(1)); loadCatalog(); poll(); store.timer = setInterval(poll, 2000);
})();
