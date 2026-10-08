const form = document.getElementById("experiment-form");
const output = document.getElementById("experiment-result");
const status = document.getElementById("experiment-status");
let frames = [];

function element(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
}

function selectedPositions(frame) {
    const body = form.elements.body.value;
    return frame.positions.filter(p => body === "all" || p.body === body);
}

function dateLabel(timestamp) {
    return new Intl.DateTimeFormat("de-DE", {
        timeZone: "UTC", day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit", hourCycle: "h23"
    }).format(new Date(timestamp));
}

function renderTable() {
    const table = element("table");
    table.appendChild(element("caption", "Berechnete Abtastpunkte (UTC)"));
    const header = element("tr");
    const columns = form.dataset.page === "ephemeris"
        ? ["Zeit (UTC)", "Himmelskörper", "Tierkreisposition", "Gate", "Linie"]
        : ["Zeit (UTC)", "Himmelskörper", "Länge", "Gate", "Linie"];
    for (const column of columns) {
        const th = element("th", column);
        th.scope = "col";
        header.appendChild(th);
    }
    table.appendChild(element("thead")).appendChild(header);
    const tbody = table.appendChild(element("tbody"));
    for (const frame of frames) {
        for (const position of selectedPositions(frame)) {
            const row = tbody.appendChild(element("tr"));
            const values = [dateLabel(frame.timestamp_utc), position.body,
                form.dataset.page === "ephemeris" ? position.zodiac : `${position.longitude.toFixed(3)}°`,
                position.gate, position.line];
            for (const value of values) row.appendChild(element("td", value));
        }
    }
    output.replaceChildren(table);
}

form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    status.setAttribute("role", "status");
    status.textContent = "Berechnung läuft …";
    try {
        const value = form.elements.start.value;
        const start = `${value}${value.length === 16 ? ":00" : ""}Z`;
        const params = new URLSearchParams({
            start, days: form.elements.days.value,
            daily: String(form.dataset.page === "ephemeris")
        });
        const response = await fetch(`/experiments/data?${params}`);
        if (!response.ok) {
            const text = await response.text();
            const doc = new DOMParser().parseFromString(text, "text/html");
            throw new Error(doc.querySelector('[role="alert"]')?.textContent ||
                `Abfrage fehlgeschlagen: HTTP ${response.status}`);
        }
        const data = await response.json();
        frames = data.frames;
        status.textContent = `${frames.length} Zeitpunkte, Abstand ${data.step_hours} Stunden. Zwischenwerte und exakte Gate-Wechsel sind nicht enthalten.`;
        renderTable();
    } catch (error) {
        output.replaceChildren();
        status.setAttribute("role", "alert");
        status.textContent = error.message;
    } finally {
        button.disabled = false;
    }
});
form.elements.body.addEventListener("change", () => { if (frames.length) renderTable(); });
