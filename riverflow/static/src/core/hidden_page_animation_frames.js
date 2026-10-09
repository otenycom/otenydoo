/** @odoo-module ignore **/
// Animation frames for a hidden page on a local development server.
//
// Why: an AI agent walks the web client in a browser pane (the Claude desktop
// app's built-in browser) that the user may hide behind another panel. A hidden
// page gets no animation frames at all (0 in 1.5 s, measured 8-Oct-2026), and
// OWL renders only inside an animation frame, so the screen of a hidden pane
// never draws: clicks and reads time out, and no wait helps. Timers still run in
// a hidden page, so while the page is hidden an animation frame request falls
// back to a short timer and OWL keeps drawing (radar pipeline applicants plan,
// release R5 follow-up; Ries, 9-Oct-2026).
//
// Scope: only a page served from localhost (a developer's laptop). Production
// and the odoo.sh copies are never served from localhost, so they keep the
// browser's own behaviour: a hidden tab does no rendering work. A visible page
// always uses the real animation frame.
//
// It must run before OWL loads: OWL captures window.requestAnimationFrame once,
// when owl.js is evaluated (Scheduler.requestAnimationFrame). So this file is
// prepended to web._assets_core and is a plain script, not an Odoo module.
(function () {
    const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1"];
    if (typeof window === "undefined" || !LOCAL_HOSTS.includes(window.location.hostname)) {
        return;
    }
    const nativeRequest = window.requestAnimationFrame.bind(window);
    const nativeCancel = window.cancelAnimationFrame.bind(window);
    const FRAME_MS = 16;
    // Every pending frame: id -> {callback, handle, timer}. A frame requested
    // while the page was visible and still pending when the page hides moves to
    // a timer (and back when the page shows), so OWL never waits for a frame that
    // a hidden page will not give.
    const pending = new Map();
    let nextId = 1;

    function schedule(id) {
        const frame = pending.get(id);
        const fire = (timestamp) => {
            if (pending.get(id) !== frame) {
                return;
            }
            pending.delete(id);
            frame.callback(timestamp);
        };
        if (document.hidden) {
            frame.timer = true;
            frame.handle = setTimeout(() => fire(performance.now()), FRAME_MS);
        } else {
            frame.timer = false;
            frame.handle = nativeRequest(fire);
        }
    }

    function unschedule(frame) {
        if (frame.timer) {
            clearTimeout(frame.handle);
        } else {
            nativeCancel(frame.handle);
        }
    }

    window.requestAnimationFrame = function (callback) {
        const id = nextId++;
        pending.set(id, { callback, handle: null, timer: false });
        schedule(id);
        return id;
    };

    window.cancelAnimationFrame = function (id) {
        const frame = pending.get(id);
        if (frame) {
            unschedule(frame);
            pending.delete(id);
        }
    };

    document.addEventListener("visibilitychange", () => {
        for (const [id, frame] of pending) {
            unschedule(frame);
            schedule(id);
        }
    });
})();
