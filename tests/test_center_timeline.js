"use strict";
const assert = require("node:assert/strict");
const {intervals, profileVersion} = require("../app/static/center-timeline.js");
const times = [0, 1000, 2000, 3000];
const frame = (...centers) => ({centers: centers.map(id => ({id}))});
const data = new Map([[0, frame("Head")], [1000, frame("Root")], [3000, frame("Head")]]);
assert.deepEqual(intervals(times, data, "Head"), [
    {start: 0, end: 1000, active: true},
    {start: 1000, end: 2000, active: null},
    {start: 2000, end: 3000, active: null},
], "Do not bridge missing samples or misrepresent them as inactive");
data.set(2000, frame("Head"));
assert.deepEqual(intervals(times, data, "Head").map(item => item.active), [true, false, true]);
assert.deepEqual(intervals(times, data, "Root").map(item => item.active), [false, true, false]);
assert.deepEqual(intervals([], data, "Head"), []);
assert.deepEqual(intervals([0], data, "Head"), []);
const base = {profile: {id: "p1", birth_utc: "2000-01-01T00:00:00Z"}, engine_version: "4"};
assert.equal(profileVersion(base), profileVersion({...base, centers: [{id: "Head"}]}));
assert.notEqual(profileVersion(base), profileVersion({...base, engine_version: "5"}));
assert.notEqual(profileVersion({...base, calculation_version: "api-a"}),
    profileVersion({...base, calculation_version: "api-b"}));
assert.notEqual(profileVersion(base), profileVersion({...base, profile: {...base.profile, id: "p2"}}));
assert.notEqual(profileVersion(base), profileVersion({...base, profile: {...base.profile, birth_utc: "2001-01-01T00:00:00Z"}}));
console.log("Center timeline: sampled intervals, gaps and profile identity passed.");

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
class Element {
    constructor() {
        this.dataset = {};
        this.children = [];
        this.listeners = {};
        this.attributes = {};
        this.style = {};
        this.value = "";
        this.textContent = "";
    }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = [...nodes]; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(key, callback) { this.listeners[key] = callback; }
    fire(key, extra = {}) { this.listeners[key]?.({preventDefault() {}, ...extra}); }
    getBoundingClientRect() { return {left: 0, width: 1000}; }
}

async function controllerTests() {
    const ids = new Map();
    const get = id => {
        if (!ids.has(id)) ids.set(id, new Element());
        return ids.get(id);
    };
    const root = get("center-timeline");
    root.dataset = {
        profileId: "p1",
        centers: JSON.stringify([{id: "Head", label: "Kopf", color: "#fff"}, {id: "Root", label: "Wurzel", color: "#aaa"}]),
    };
    const boxes = ["Head", "Root"].map(id => Object.assign(new Element(), {checked: true, dataset: {center: id}}));
    root.querySelectorAll = query => query === "input[data-center]" ? boxes : [];
    root.querySelector = query => boxes.find(box => query.includes(`"${box.dataset.center}"`));
    const form = get("center-range");
    const fields = {
        start: Object.assign(new Element(), {value: "2026-10-25T00:00"}),
        end: new Element(),
        samples: Object.assign(new Element(), {value: "64"}),
    };
    form.elements = {namedItem: name => fields[name]};
    const pending = [];
    let calls = 0;
    const response = (payload, status = 200) => ({ok: status === 200, status, json: async () => payload});
    const fetch = async url => {
        const parsed = new URL(url, "http://localhost");
        if (parsed.pathname === "/experiments/timeline") {
            const start = parsed.searchParams.get("start");
            const end = parsed.searchParams.get("end");
            const mid = new Date((Date.parse(start) + Date.parse(end)) / 2).toISOString();
            return response({start, end, timestamps: [start, mid, end], step_seconds: 3600});
        }
        calls++;
        return new Promise(resolve => pending.push({
            timestamp: parsed.searchParams.get("timestamp"), graph: parsed.searchParams.get("graph"),
            resolve: (payload, status) => resolve(response(payload, status)),
        }));
    };
    const waitFor = async predicate => {
        for (let i = 0; i < 100; i++) {
            if (predicate()) return;
            await new Promise(resolve => setTimeout(resolve, 5));
        }
        throw new Error("Controller did not reach expected state");
    };
    const fulfill = (request, changed = false) => request.resolve({
        ...base, profile: {...base.profile, id: changed ? "p2" : "p1"},
        timestamp: request.timestamp, ...frame("Head"), svg: "<svg>selected graph</svg>",
    });
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../app/static/center-timeline.js"), "utf8"), {
        document: {getElementById: get, createElement: () => new Element()},
        fetch, URLSearchParams, Date, AbortController,
        setTimeout: callback => setTimeout(callback, 1), clearTimeout,
    });
    for (let i = 0; i < 3; i++) {
        await waitFor(() => pending.length === 1);
        const request = pending.shift();
        assert.equal(request.graph, i === 0 ? "true" : "false");
        fulfill(request);
    }
    await waitFor(() => get("center-status").textContent.includes("vollständig"));
    assert.equal(get("center-progress").value, 3);
    assert.equal(get("center-tracks").children.length, 3, "Two stacked tracks plus the time axis");
    assert.match(get("center-graph").innerHTML, /selected graph/);
    boxes[0].checked = false;
    boxes[0].fire("change");
    assert.equal(get("center-tracks").children.length, 2);
    assert.equal(calls, 3, "Toggling tracks must not recalculate");
    form.fire("submit");
    await waitFor(() => pending.length === 1);
    fulfill(pending.shift());
    await waitFor(() => get("center-status").textContent.includes("vollständig"));
    assert.equal(calls, 4, "Reload validates first sample, then reuses cached window samples");
    get("center-cursor").value = "250";
    get("center-cursor").fire("input");
    await waitFor(() => pending.length === 1);
    const oldSelection = pending.shift();
    get("center-cursor").value = "750";
    get("center-cursor").fire("input");
    await waitFor(() => pending.length === 1);
    const newSelection = pending.shift();
    fulfill(oldSelection);
    fulfill(newSelection);
    await waitFor(() => get("center-graph-status").textContent.includes("Definiert"));
    assert.match(get("center-graph-status").textContent, /30\.10\.2026/, "Only the latest cursor response is displayed");

    form.fire("submit");
    await waitFor(() => pending.length === 1);
    pending.shift().resolve({detail: "Dienst offline"}, 502);
    await waitFor(() => get("center-status").textContent.includes("Dienst offline"));
    assert.equal(get("center-status").attributes.role, "alert");
    assert.equal(get("center-cursor").disabled, true);
    assert.match(get("center-graph").children[0].textContent, /Keine gültigen/);

    fields.start.value = "2027-01-01T00:00";
    fields.end.value = "2027-01-08T00:00";
    form.fire("submit");
    await waitFor(() => pending.length === 1);
    const busy = pending.shift();
    busy.resolve({detail: "belegt"}, 503);
    await waitFor(() => pending.length === 1);
    assert.equal(pending[0].timestamp, busy.timestamp);
    fulfill(pending.shift());
    await waitFor(() => pending.length === 1);
    fulfill(pending.shift(), true);
    await waitFor(() => get("center-status").textContent.includes("Geburtsprofil wurde geändert"));
    assert.equal(get("center-cursor").disabled, true);
    assert.match(get("center-graph").children[0].textContent, /Darstellung verworfen/);
    console.log("Center timeline controller: loading, tracks, exact selection, stale requests, retry and profile changes passed.");
}

controllerTests().catch(error => { console.error(error); process.exitCode = 1; });
