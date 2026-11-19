function renderRosterRating(rating) {
    const section = document.createElement("section");
    section.className = "border rounded p-3 mb-3 small";
    section.setAttribute("aria-label", "本队补强评级");
    const heading = document.createElement("div");
    heading.className = "d-flex flex-wrap align-items-center gap-2 mb-2";
    const badge = document.createElement("strong");
    const tones = {S: "success", A: "success", B: "primary", C: "warning", D: "danger", E: "danger"};
    badge.className = `badge fs-5 text-bg-${tones[rating.grade] || "secondary"}`;
    badge.textContent = rating.grade;
    const title = document.createElement("strong");
    title.textContent = `本队补强评级 · ${rating.label} · ${rating.score}/100`;
    heading.append(badge, title);
    const summary = document.createElement("p");
    summary.className = "mb-2";
    summary.textContent = rating.summary;
    const details = document.createElement("details");
    const caption = document.createElement("summary");
    caption.textContent = "查看评级依据";
    const method = document.createElement("p");
    method.className = "text-secondary mt-2 mb-2";
    method.textContent = `${rating.method} · 证据充分程度：${rating.confidence}`;
    const dimensions = document.createElement("dl");
    dimensions.className = "mb-2";
    for (const dimension of rating.dimensions) {
        const label = document.createElement("dt");
        label.textContent = `${dimension.label} ${dimension.score}/${dimension.max}`;
        const reason = document.createElement("dd");
        reason.textContent = dimension.reason;
        dimensions.append(label, reason);
    }
    const limits = document.createElement("ul");
    limits.className = "text-warning-emphasis ps-3";
    for (const limit of rating.limits) {
        const item = document.createElement("li");
        item.textContent = limit;
        limits.append(item);
    }
    const note = document.createElement("p");
    note.className = "text-secondary mb-1";
    note.textContent = rating.note;
    const scale = document.createElement("p");
    scale.className = "text-secondary mb-0";
    scale.textContent = rating.scale;
    details.append(caption, method, dimensions, limits, note, scale);
    section.append(heading, summary, details);
    return section;
}

async function requestDecisionAnalysis(url, payload, csrfToken, button, box, isCurrent) {
    button.disabled = true;
    box.textContent = "正在生成分析…";
    try {
        const response = await fetch(url, {
            method: "POST",
            headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
            body: JSON.stringify(payload)
        });
        const analysis = await response.json();
        if (!response.ok) throw new Error(analysis.error || "分析生成失败");
        if (!isCurrent()) return;
        const title = document.createElement("p");
        title.className = "fw-semibold mb-2";
        title.textContent = analysis.source === "model" ? "本队决策建议 · AI 整理" : "本队决策建议 · 规则生成";
        const list = document.createElement("ul");
        for (const item of analysis.items) {
            const li = document.createElement("li");
            li.className = "mb-2";
            const heading = document.createElement("strong");
            heading.textContent = item.title;
            const fact = document.createElement("div");
            fact.textContent = item.fact;
            const action = document.createElement("div");
            action.className = "text-secondary small";
            action.textContent = item.action;
            li.append(heading, fact, action);
            list.append(li);
        }
        const checks = document.createElement("ul");
        checks.className = "text-danger";
        for (const check of analysis.checks) {
            const li = document.createElement("li");
            li.textContent = check;
            checks.append(li);
        }
        const comparison = document.createElement("section");
        const heading = document.createElement("h3");
        heading.className = "h6";
        heading.textContent = "球队需求与球员对比";
        const context = document.createElement("p");
        context.className = "small text-secondary mb-2";
        const priorities = analysis.priorities.map(row =>
            `${row.label}场均 ${row.ours.toFixed(1)}（对手 ${row.opponents.toFixed(1)}）`);
        const gaps = analysis.team_sample < 3 ? "暂无足量的双方技术统计。"
            : priorities.length ? priorities.join("；") : "当前样本未显示明显技术差距。";
        context.textContent = `本队有 ${analysis.team_sample} 场完赛具备双方统计。${gaps}`;
        comparison.append(heading, context);
        if (analysis.profiles.length) {
            const scroll = document.createElement("div");
            scroll.className = "table-responsive";
            const table = document.createElement("table");
            table.className = "table table-sm align-middle small";
            const columns = Object.entries(analysis.metric_labels);
            const headers = table.createTHead().insertRow();
            for (const col of ["球员", "角色", "样本", ...columns.map(([, label]) => label)]) {
                const th = document.createElement("th");
                th.scope = "col";
                th.className = "text-nowrap";
                th.textContent = col;
                headers.append(th);
            }
            const tbody = table.createTBody();
            for (const profile of analysis.profiles) {
                const row = tbody.insertRow();
                const sample = profile.games ? `${profile.games} 场 · ${profile.minutes} 分钟${profile.reliable ? "" : " · 样本少"}` : "无出场样本";
                for (const value of [
                    `${profile.name} · ${profile.position}`, profile.role, sample,
                    ...columns.map(([key]) => profile.metrics[key] == null ? "—" : profile.metrics[key].toFixed(1))
                ]) {
                    row.insertCell().textContent = value;
                }
            }
            scroll.append(table);
            const description = document.createElement("p");
            description.className = "text-secondary small";
            description.textContent = "球员数据按赛季每 36 分钟折算。用于比较历史表现，实际效果取决于上场时间和轮换角色。";
            comparison.append(scroll, description);
        }
        box.replaceChildren(title);
        if (analysis.rating) box.append(renderRosterRating(analysis.rating));
        box.append(checks, list, comparison);
    } catch (error) {
        if (isCurrent()) box.textContent = error.message;
    } finally {
        button.disabled = false;
    }
}
async function postRosterDecision(url, payload, csrfToken) {
    const response = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
        body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || result.reasons?.join("；") || "操作失败，请重试。");
    return result;
}

function bindRosterAssistant({endpoint, season, csrfToken, applyLabel, apply}) {
    const generate = document.getElementById("generateRosterPlans");
    const status = document.getElementById("rosterAssistantStatus");
    const results = document.getElementById("rosterPlanCards");
    const labels = {points: "得分", rebounds: "篮板", assists: "助攻", threes: "三分命中", turnovers: "失误"};
    const showStatus = (text, color = "text-secondary") => {
        status.textContent = text;
        status.className = "mb-0 mt-2 small " + color;
    };
    const lockActions = (locked) => {
        generate.disabled = locked;
        results.querySelectorAll("button").forEach(button => { button.disabled = locked; });
    };

    generate.addEventListener("click", async () => {
        lockActions(true);
        showStatus("正在生成方案…");
        results.replaceChildren();
        try {
            const analysis = await postRosterDecision(endpoint, {season}, csrfToken);
            if (!analysis.plans.length) {
                showStatus(analysis.message);
                return;
            }
            const hasCourtRole = Boolean(analysis.plans[0].value?.usage);
            const source = analysis.source === "model" ? (hasCourtRole ? "AI 排序与比赛用法" : "AI 排序") : "数据规则排序";
            showStatus(`${source} · ${analysis.plans.length} 套可选方案 · 本队统计样本 ${analysis.team_sample} 场`);
            if (analysis.needs.length) {
                const need = document.createElement("p");
                need.className = "text-secondary small mt-2 mb-0";
                need.textContent = analysis.needs.join(" ");
                results.append(need);
            }
            const grid = document.createElement("div");
            grid.className = "row row-cols-1 row-cols-xl-3 g-3 mt-1";
            for (const plan of analysis.plans) {
                const card = document.createElement("article");
                card.className = "card card-body h-100 border-secondary-subtle";
                const title = document.createElement("h3");
                title.className = "h6";
                title.textContent = plan.title;
                const subtitle = document.createElement("p");
                subtitle.className = "small text-secondary mb-2";
                subtitle.textContent = plan.subtitle;
                card.append(title, subtitle);
                if (plan.rating) card.append(renderRosterRating(plan.rating));
                if (plan.value) {
                    const value = document.createElement("div");
                    value.className = "rounded bg-primary-subtle text-primary-emphasis p-2 mb-3 small";
                    const heading = document.createElement("strong");
                    heading.className = "d-block mb-1";
                    heading.textContent = "对本队的价值";
                    value.append(heading, document.createTextNode(plan.value.summary));
                    const details = document.createElement("dl");
                    details.className = "small mb-2";
                    for (const [key, label] of Object.entries({
                        usage: "未来比赛怎么用", court: "数据依据", verify: "教练怎么验证",
                        rotation: "阵容轮换", budget: "球队薪资", tradeoff: "主要取舍"
                    })) {
                        if (!plan.value[key]) continue;
                        const term = document.createElement("dt");
                        term.textContent = label;
                        const description = document.createElement("dd");
                        description.textContent = plan.value[key];
                        details.append(term, description);
                    }
                    card.append(value, details);
                } else {
                    const notes = document.createElement("ul");
                    notes.className = "small ps-3 mb-2";
                    for (const note of plan.notes) {
                        const row = document.createElement("li");
                        row.className = "mb-1";
                        row.textContent = note;
                        notes.append(row);
                    }
                    card.append(notes);
                }
                const profiles = document.createElement("details");
                profiles.className = "small mb-3";
                const caption = document.createElement("summary");
                caption.textContent = "球员样本（每 36 分钟）";
                profiles.append(caption);
                for (const profile of plan.profiles) {
                    const line = document.createElement("p");
                    line.className = "text-secondary mt-1 mb-0";
                    const metrics = Object.entries(labels)
                        .filter(([key]) => profile.metrics[key] != null)
                        .map(([key, label]) => label + " " + profile.metrics[key].toFixed(1));
                    const sample = profile.games
                        ? `${profile.games} 场 / ${profile.minutes} 分钟${profile.reliable ? "" : "（样本少）"} · ${metrics.join("、")}`
                        : "无出场样本";
                    line.textContent = `${profile.name} · ${profile.role} · ${sample}`;
                    profiles.append(line);
                }
                const button = document.createElement("button");
                button.type = "button";
                button.className = "btn btn-sm btn-outline-primary align-self-start mt-auto";
                button.textContent = applyLabel;
                button.addEventListener("click", async () => {
                    lockActions(true);
                    try {
                        const result = await apply(plan);
                        if (result) {
                            results.replaceChildren();
                            showStatus(result.message, "text-success");
                        }
                    } catch (error) {
                        showStatus(error.message, "text-danger");
                    } finally {
                        lockActions(false);
                    }
                });
                card.append(profiles, button);
                const col = document.createElement("div");
                col.className = "col";
                col.append(card);
                grid.append(col);
            }
            results.append(grid);
        } catch (error) {
            showStatus(error.message, "text-danger");
        } finally {
            lockActions(false);
        }
    });
}
