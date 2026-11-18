for (const search of document.querySelectorAll("[data-options-for]")) {
    const select = document.getElementById(search.dataset.optionsFor);
    const status = document.querySelector(`[data-option-status="${select.id}"]`);
    const options = Array.from(select.options);
    search.hidden = false;
    search.addEventListener("input", () => {
        const query = search.value.trim().toLocaleLowerCase();
        const selected = select.value;
        const shown = options.filter(option => option.value && option.textContent.toLocaleLowerCase().includes(query));
        const list = new Set([options[0], ...shown, ...options.filter(option => option.value === selected)]);
        select.replaceChildren(...options.filter(option => list.has(option)));
        select.value = selected;
        status.textContent = query ? `找到 ${shown.length} 名球员` : "";
    });
}
