const reloadSeconds = Number(document.currentScript.dataset.reloadSeconds);
window.setTimeout(() => {
    window.location.reload();
}, reloadSeconds * 1000);
