const transitTable = document.querySelector(".transit-table");
let sortColumn = null;
let sortDirection = 1;

function sortTransitRows() {
    if (!sortColumn) return;
    const rows = Array.from(transitTable.tBodies[0].rows);
    rows.sort((first, second) => {
        const firstRaw = first.dataset[sortColumn];
        const secondRaw = second.dataset[sortColumn];
        // Uncomputed values stay last in either direction.
        if (firstRaw === undefined || secondRaw === undefined) {
            return Number(firstRaw === undefined) - Number(secondRaw === undefined);
        }
        return (Number(firstRaw) - Number(secondRaw)) * sortDirection;
    });
    for (const row of rows) transitTable.tBodies[0].appendChild(row);
}

for (const button of transitTable.querySelectorAll("[data-sort]")) {
    button.addEventListener("click", () => {
        sortDirection = sortColumn === button.dataset.sort ? -sortDirection : 1;
        sortColumn = button.dataset.sort;
        for (const other of transitTable.querySelectorAll("[data-sort]")) {
            const active = other === button;
            other.closest("th").setAttribute("aria-sort",
                active ? (sortDirection === 1 ? "ascending" : "descending") : "none");
            other.querySelector("span").textContent =
                active ? (sortDirection === 1 ? " ↑" : " ↓") : " ↕";
        }
        sortTransitRows();
    });
}

async function loadForecast() {
    const error = document.getElementById("forecast-error");
    try {
        const response = await fetch("/transit/next-gates");
        if (!response.ok) throw new Error(`Gate-Berechnung: HTTP ${response.status}`);
        const result = await response.json();
        for (const change of result.changes) {
            const row = Array.from(document.querySelectorAll("[data-body]"))
                .find(item => item.dataset.body === change.body &&
                    Number(item.dataset.gate) === change.current_gate);
            if (!row) continue;
            const link = document.createElement("a");
            link.href = `/gates#gate-${change.next_gate}`;
            link.textContent = change.next_gate;
            row.querySelector(".next-gate").replaceChildren(link);
            row.dataset.nextGate = String(change.next_gate);
            const eta = new Date(change.eta_utc);
            row.dataset.eta = String(eta.getTime());
            row.querySelector(".gate-eta").textContent = new Intl.DateTimeFormat("de-DE", {
                timeZone: "UTC", day: "2-digit", month: "2-digit", year: "numeric",
                hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
            }).format(eta);
        }
        sortTransitRows();
        if (result.error) throw new Error(result.error);
        if (result.pending) window.setTimeout(loadForecast, 3000);
    } catch (failure) {
        error.hidden = false;
        error.textContent = failure.message;
    }
}
loadForecast();
