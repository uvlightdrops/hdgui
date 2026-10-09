(() => {
    "use strict";
    const toggle = document.getElementById("catalog-activations");
    if (!toggle) return;
    const status = document.getElementById("catalog-status");
    const elements = document.querySelectorAll("[data-activation-gate]");
    const labels = {personality: "Persönlichkeit", design: "Design"};
    let activations = null;
    let loading = false;

    function render() {
        for (const element of elements) {
            const sources = toggle.checked && activations
                ? activations.get(Number(element.dataset.activationGate)) || [] : [];
            element.classList.toggle("catalog-active", sources.length > 0);
            element.classList.toggle("catalog-personality", sources.length === 1 && sources[0] === "personality");
            element.classList.toggle("catalog-design", sources.length === 1 && sources[0] === "design");
            element.classList.toggle("catalog-both", sources.length === 2);
            const badge = element.querySelector(".catalog-activation");
            if (badge) {
                badge.hidden = !sources.length;
                badge.textContent = sources.length ? `Aktiv: ${sources.map(source => labels[source]).join(" + ")}` : "";
            }
        }
    }

    async function update() {
        render();
        if (!toggle.checked) {
            status.textContent = "Profilmarkierung ausgeschaltet.";
            return;
        }
        if (loading) {
            status.textContent = "Geburtsaktivierungen werden geladen …";
            return;
        }
        if (!activations) {
            loading = true;
            status.textContent = "Geburtsaktivierungen werden geladen …";
            try {
                const response = await fetch("/profiles/current/overlay", {cache: "no-store"});
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || "Geburtsaktivierungen konnten nicht geladen werden.");
                if (data.profile?.id !== toggle.dataset.profileId)
                    throw new Error("Das aktuelle Profil wurde geändert. Bitte die Seite neu laden.");
                if (!Array.isArray(data.gates) || !data.gates.length || data.gates.some(item =>
                    !Number.isInteger(item.gate) || item.gate < 1 || item.gate > 64 ||
                    !Array.isArray(item.sources) || !item.sources.length ||
                    item.sources.some(source => !Object.hasOwn(labels, source))))
                    throw new Error("Ungültige Geburtsaktivierungen erhalten.");
                activations = new Map(data.gates.map(item => [item.gate, item.sources]));
            } catch (error) {
                toggle.checked = false;
                status.setAttribute("role", "alert");
                status.textContent = `${error.message} Zum Wiederholen die Markierung einschalten.`;
                render();
                return;
            } finally {
                loading = false;
            }
        }
        status.setAttribute("role", "status");
        status.textContent = toggle.checked
            ? `${activations.size} aktive Tore markiert.` : "Profilmarkierung ausgeschaltet.";
        render();
    }
    toggle.addEventListener("change", update);
    update();
})();
