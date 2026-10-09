const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("app/static/gate-catalog.js", "utf8");
function setup() {
    const toggle = {checked: true, dataset: {profileId: "p1"},
        addEventListener(name, listener) { this.change = listener; }};
    const status = {textContent: "", setAttribute(name, value) { this[name] = value; }};
    const elements = [1, 2, 3, 4].flatMap(gate => [true, false].map(article => {
        const classes = new Set();
        const badge = article ? {hidden: true, textContent: ""} : null;
        return {dataset: {activationGate: String(gate)}, classes, badge,
            classList: {toggle(name, on) { if (on) classes.add(name); else classes.delete(name); }},
            querySelector() { return badge; }};
    }));
    const requests = [];
    vm.runInNewContext(source, {
        document: {getElementById(id) { return id === "catalog-activations" ? toggle : status; },
            querySelectorAll() { return elements; }},
        fetch(url, options) {
            assert.equal(url, "/profiles/current/overlay");
            assert.equal(options.cache, "no-store");
            return new Promise(resolve => requests.push((data, ok = true) =>
                resolve({ok, json: async () => data})));
        },
    });
    return {toggle, status, elements, requests};
}
const data = {profile: {id: "p1"}, gates: [
    {gate: 1, sources: ["personality"]}, {gate: 2, sources: ["design"]},
    {gate: 3, sources: ["personality", "design"]},
]};
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    const page = setup();
    assert.equal(page.requests.length, 1);
    page.toggle.checked = false;
    await page.toggle.change();
    page.requests.shift()(data);
    await flush();
    assert(page.elements.every(element => !element.classes.has("catalog-active")));
    page.toggle.checked = true;
    await page.toggle.change();
    assert.equal(page.requests.length, 0, "Re-enabling uses cached activations");
    for (const [index, color] of [[0, "personality"], [2, "design"], [4, "both"]]) {
        for (const element of page.elements.slice(index, index + 2)) {
            assert(element.classes.has(`catalog-${color}`));
            assert(element.classes.has("catalog-active"));
        }
        assert.equal(page.elements[index].badge.hidden, false);
        assert.match(page.elements[index].badge.textContent, /Aktiv:/);
    }
    assert(!page.elements[6].classes.has("catalog-active"));
    page.toggle.checked = false;
    await page.toggle.change();
    assert(page.elements.every(element => !element.classes.has("catalog-active")));
    assert(page.elements.filter(element => element.badge).every(element => element.badge.hidden));

    for (const [payload, ok, message] of [
        [{detail: "Dienst nicht erreichbar"}, false, /Dienst nicht erreichbar/],
        [{...data, profile: {id: "p2"}}, true, /Profil wurde geändert/],
        [{...data, gates: [{gate: 65, sources: ["design"]}]}, true, /Ungültige/],
    ]) {
        const failed = setup();
        failed.requests.shift()(payload, ok);
        await flush();
        assert.equal(failed.toggle.checked, false);
        assert.equal(failed.status.role, "alert");
        assert.match(failed.status.textContent, message);
        failed.toggle.checked = true;
        const retry = failed.toggle.change();
        failed.requests.shift()(data);
        await retry;
        assert.equal(failed.status.role, "status");
        assert(failed.elements[0].classes.has("catalog-active"));
    }
    console.log("Gate catalog: activation colors, toggling, retained cache, errors and retry passed.");
})().catch(error => { console.error(error); process.exitCode = 1; });
