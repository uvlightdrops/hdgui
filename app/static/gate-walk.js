(() => {
    "use strict";
    const root = document.getElementById("gate-walk");
    const order = JSON.parse(root.dataset.order);
    const descriptions = JSON.parse(document.getElementById("walk-descriptions").textContent);
    const positions = order.map(gate => {
        const circle = root.querySelector(`[data-walk-gate="${gate}"] circle`);
        return [Number(circle.getAttribute("cx")), Number(circle.getAttribute("cy"))];
    });
    const dot = document.getElementById("gate-walk-dot");
    const trail = document.getElementById("gate-walk-trail");
    const zodiacDot = document.getElementById("walk-zodiac-dot");
    const zodiacTrail = document.getElementById("walk-zodiac-trail");
    const zodiacRadius = document.getElementById("walk-zodiac-radius");
    const zodiacSectors = order.map(gate =>
        root.querySelector(`[data-zodiac-gate="${gate}"]`));
    function zodiacPoint(step) {
        const angle = (302 + (step + .5) * 360 / order.length - 90) * Math.PI / 180;
        return [120 + 71 * Math.cos(angle), 120 + 71 * Math.sin(angle)];
    }
    const play = document.getElementById("walk-play");
    const speed = document.getElementById("walk-speed");
    const status = document.getElementById("walk-status");
    const svg = document.getElementById("gate-walk-svg");
    const viewport = root.querySelector(".gate-walk-graph");
    const zodiacSvg = document.getElementById("walk-zodiac-svg");
    const zodiacViewport = root.querySelector(".gate-walk-zodiac");
    const zoomInput = document.getElementById("walk-zoom");
    let zoom = 1, left = 0, top = 0, drag = null;

    function updateView() {
        const width = 240 / zoom, height = 320 / zoom;
        left = Math.max(0, Math.min(240 - width, left));
        top = Math.max(0, Math.min(320 - height, top));
        svg.setAttribute("viewBox", `${left} ${top} ${width} ${height}`);
        zodiacSvg.setAttribute("viewBox", `${left} ${top * .75} ${width} ${height * .75}`);
        zoomInput.value = zoom;
        document.getElementById("walk-zoom-value").textContent = `${Math.round(zoom * 100)} %`;
        document.getElementById("walk-zoom-out").disabled = zoom === 1;
        document.getElementById("walk-zoom-in").disabled = zoom === 6;
    }

    function setZoom(value, anchorX = left + 120 / zoom, anchorY = top + 160 / zoom) {
        const next = Math.max(1, Math.min(6, value));
        left = anchorX - (anchorX - left) * zoom / next;
        top = anchorY - (anchorY - top) * zoom / next;
        zoom = next;
        updateView();
    }

    function svgPoint(event, targetSvg) {
        const point = targetSvg.createSVGPoint();
        point.x = event.clientX;
        point.y = event.clientY;
        return point.matrixTransform(targetSvg.getScreenCTM().inverse());
    }

    zoomInput.addEventListener("input", () => setZoom(Number(zoomInput.value)));
    document.getElementById("walk-zoom-in").addEventListener("click", () => setZoom(zoom * 1.25));
    document.getElementById("walk-zoom-out").addEventListener("click", () => setZoom(zoom / 1.25));
    function fit() { zoom = 1; left = 0; top = 0; updateView(); }
    document.getElementById("walk-fit").addEventListener("click", fit);
    for (const [surface, targetSvg, yScale] of [
        [viewport, svg, 1], [zodiacViewport, zodiacSvg, 4 / 3]
    ]) {
    surface.addEventListener("wheel", event => {
        event.preventDefault();
        const point = svgPoint(event, targetSvg);
        setZoom(zoom * Math.exp(-event.deltaY * .002), point.x, point.y * yScale);
    }, {passive: false});
    surface.addEventListener("pointerdown", event => {
        if (event.button !== 0 || drag) return;
        surface.focus({preventScroll: true});
        const matrix = targetSvg.getScreenCTM().inverse();
        drag = {id: event.pointerId, x: event.clientX, y: event.clientY, left, top, matrix, yScale};
        surface.setPointerCapture(event.pointerId);
    });
    surface.addEventListener("pointermove", event => {
        if (!drag || drag.id !== event.pointerId) return;
        const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
        left = drag.left - dx * drag.matrix.a - dy * drag.matrix.c;
        top = drag.top - (dx * drag.matrix.b + dy * drag.matrix.d) * drag.yScale;
        updateView();
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
        surface.addEventListener(name, event => {
            if (drag?.id === event.pointerId) drag = null;
        });
    }
    surface.addEventListener("keydown", event => {
        const actions = {
            "+": () => setZoom(zoom * 1.25), "=": () => setZoom(zoom * 1.25),
            "-": () => setZoom(zoom / 1.25), Home: fit,
            ArrowLeft: () => { left -= 24 / zoom; updateView(); },
            ArrowRight: () => { left += 24 / zoom; updateView(); },
            ArrowUp: () => { top -= 32 / zoom; updateView(); },
            ArrowDown: () => { top += 32 / zoom; updateView(); }
        };
        if (actions[event.key]) { event.preventDefault(); actions[event.key](); }
    });
    }
    let progress = 0, rounds = 0, playing = false, previous = null, animation = null, lastGate = -1;

    function render() {
        const index = Math.min(order.length - 1, Math.floor(progress));
        const fraction = progress - Math.floor(progress);
        const start = positions[index];
        const target = positions[(index + 1) % order.length];
        const point = progress >= order.length ? positions[0] : [
            start[0] + (target[0] - start[0]) * fraction,
            start[1] + (target[1] - start[1]) * fraction
        ];
        dot.setAttribute("cx", point[0]);
        dot.setAttribute("cy", point[1]);
        const visited = rounds > 0 ? [...positions, positions[0]]
            : [...positions.slice(0, Math.floor(progress) + 1), point];
        trail.setAttribute("points", visited.map(p => p.join(",")).join(" "));
        const zodiac = zodiacPoint(progress);
        zodiacDot.setAttribute("cx", zodiac[0]);
        zodiacDot.setAttribute("cy", zodiac[1]);
        zodiacRadius.setAttribute("x2", zodiac[0]);
        zodiacRadius.setAttribute("y2", zodiac[1]);
        const circlePoints = [];
        const trailProgress = rounds > 0 ? order.length : progress;
        for (let step = 0; step < trailProgress; step += .1) circlePoints.push(zodiacPoint(step));
        circlePoints.push(zodiacPoint(trailProgress));
        zodiacTrail.setAttribute("points", circlePoints.map(p => p.join(",")).join(" "));
        const gate = progress >= order.length ? order[0] : order[index];
        if (gate !== lastGate || progress >= order.length) {
            const description = descriptions[gate];
            document.getElementById("walk-gate-name").textContent = `Gate ${gate} · ${description.name}`;
            document.getElementById("walk-gate-description").textContent = description.description;
            document.getElementById("walk-gate-channel").textContent = description.channel;
            document.getElementById("walk-gate-link").href = `/gates#gate-${gate}`;
            zodiacSectors.forEach((sector, step) => {
                sector.setAttribute("fill-opacity", rounds > 0 || step <= Math.floor(progress) ? ".65" : "0");
            });
            status.textContent = progress >= order.length
                ? "Runde vollständig · Gate 41"
                : `Gate ${gate} · ${index + 1}/64 · Runde ${rounds + 1}`;
            lastGate = gate;
        }
    }

    function setPlaying(value) {
        playing = value;
        play.textContent = value ? "Pause" : "Abspielen";
        play.setAttribute("aria-pressed", String(value));
        previous = null;
        if (animation !== null) cancelAnimationFrame(animation);
        animation = value ? requestAnimationFrame(tick) : null;
    }

    function tick(time) {
        if (!playing) return;
        if (previous !== null) {
            progress += (time - previous) / 1000 * Number(speed.value);
            if (progress >= order.length) {
                rounds += Math.floor(progress / order.length);
                progress %= order.length;
                lastGate = -1;
            }
        }
        previous = time;
        render();
        updateView();
        animation = requestAnimationFrame(tick);
    }

    play.addEventListener("click", () => {
        setPlaying(!playing);
    });
    document.getElementById("walk-stop").addEventListener("click", () => setPlaying(false));
    document.getElementById("walk-reset").addEventListener("click", () => {
        setPlaying(false);
        progress = 0;
        rounds = 0;
        lastGate = -1;
        render();
    });
    speed.addEventListener("input", () => {
        document.getElementById("walk-speed-value").textContent =
            `${Number(speed.value).toLocaleString("de-DE")} Tore/s`;
        previous = null;
    });
    document.addEventListener("visibilitychange", () => { previous = null; });
    render();
    updateView();
})();
