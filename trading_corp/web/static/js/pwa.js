/* Service worker registration + install-prompt UI helpers.
 *
 * iOS Safari does NOT fire `beforeinstallprompt` — it only ever shows the
 * Add-to-Home-Screen flow through the share sheet. Android Chrome does
 * fire that event and we capture it for the optional install banner.
 *
 * The SW has to be served at `/sw.js` (root scope) so it can intercept
 * fetches for the whole app. See the Caddy/route config — we route
 * `/sw.js` to the static file rather than serving it under `/static/`.
 */

(function () {
    if ("serviceWorker" in navigator) {
        // Defer until window load so SW registration doesn't fight initial paint.
        window.addEventListener("load", function () {
            navigator.serviceWorker
                .register("/sw.js", { scope: "/" })
                .then(function (reg) {
                    console.log("[pwa] service worker registered, scope:", reg.scope);
                    // Auto-update check every hour. SW.activate() handles cache cleanup.
                    setInterval(function () { reg.update(); }, 60 * 60 * 1000);
                })
                .catch(function (err) {
                    console.warn("[pwa] service worker registration failed:", err);
                });
        });
    }

    // Capture the Android install prompt so we can show our own button later
    // (right now we don't expose one — keep simple). iOS users get the
    // standard "Share → Add to Home Screen" flow regardless.
    let deferredInstallPrompt = null;
    window.addEventListener("beforeinstallprompt", function (e) {
        e.preventDefault();
        deferredInstallPrompt = e;
        window.tcInstallPromptAvailable = true;
        // Future: emit a custom event so a banner can hook in.
        document.dispatchEvent(new CustomEvent("tc-install-available"));
    });

    // Public hook so a UI button can trigger the install if available.
    window.tcShowInstallPrompt = async function () {
        if (!deferredInstallPrompt) return false;
        deferredInstallPrompt.prompt();
        const choice = await deferredInstallPrompt.userChoice;
        deferredInstallPrompt = null;
        window.tcInstallPromptAvailable = false;
        return choice.outcome === "accepted";
    };

    // Detect standalone (installed) mode for conditional UI tweaks.
    function isStandalone() {
        return (
            window.matchMedia("(display-mode: standalone)").matches ||
            // iOS-specific check — `navigator.standalone` exists only in iOS Safari
            window.navigator.standalone === true
        );
    }
    if (isStandalone()) {
        document.documentElement.classList.add("pwa-installed");
    }
})();
