function bindBudgetPreview(button, box, endpoint, csrfToken, getPayload) {
    const dollars = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0});
    const money = value => dollars.format(value);
    const depth = entry => Object.entries(entry.depth).sort().map(([position, count]) => position + " " + count).join(" · ") || "无合同覆盖";
    const element = (tag, content, className) => {
        const node = document.createElement(tag);
        node.textContent = content;
        if (className) node.className = className;
        return node;
    };

    button.addEventListener("click", async () => {
        const payload = getPayload();
        if (!payload) {
            box.textContent = "请先选择球员。";
            return;
        }
        const signature = JSON.stringify(payload);
        button.disabled = true;
        box.textContent = "正在计算预算…";
        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
                body: signature,
            });
            const preview = await response.json();
            if (!response.ok) throw new Error(preview.error || "暂时无法预览预算");
            if (signature !== JSON.stringify(getPayload())) {
                box.textContent = "选项已变更，请重新预览。";
                return;
            }

            box.replaceChildren(element("h3", preview.label + " · 逐年预览", "h6 mt-3"));
            box.append(element("p", "位置人数按各年仍有合同的球员计算。预算预览按当前方案，修改后请重新计算。", "small text-secondary"));
            if (preview.reasons.length) {
                const reasons = element("ul", "", "alert alert-warning ps-4");
                for (const reason of preview.reasons) reasons.append(element("li", reason));
                box.append(reasons);
            }
            const summary = element("div", "", "table-responsive");
            const table = element("table", "", "table table-sm align-middle");
            const head = table.createTHead().insertRow();
            const hasAlternate = preview.years.some(row => row.alternate);
            const columns = ["赛季", "维持现状", "应用方案后", "方案后工资帽空间", "方案后距税线", "合同覆盖变化"];
            if (hasAlternate) columns.push("改签一年");
            for (const column of columns) head.append(element("th", column));
            const body = table.createTBody();
            for (const row of preview.years) {
                const cells = [
                    row.season, money(row.current.payroll),
                    row.projected ? money(row.projected.payroll) : "—",
                    row.cap && row.projected ? money(row.cap.cap_amount - row.projected.payroll) : row.cap ? "—" : "未公布",
                    row.cap && row.projected ? money(row.cap.luxury_tax_line - row.projected.payroll) : row.cap ? "—" : "未公布",
                    row.projected ? depth(row.current) + " → " + depth(row.projected) : depth(row.current),
                ];
                if (hasAlternate) cells.push(row.alternate ? money(row.alternate.payroll) : "—");
                const tr = body.insertRow();
                for (const cell of cells) tr.append(element("td", cell));
            }
            summary.append(table);
            box.append(summary);
        } catch (error) {
            box.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    });
}
