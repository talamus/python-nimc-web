// Drives every .nimc-console panel on the page: streams an action's output over
// Server-Sent Events, renders ANSI colours, and stops the run via /stop.
import { AnsiUp } from "/console-static/js/ansi_up.js";

function initConsole(root) {
  const slug = root.dataset.slug;
  const output = root.querySelector(".nimc-console__output");
  const status = root.querySelector(".nimc-console__status");
  const stopBtn = root.querySelector(".nimc-console__stop");
  const actionBtns = root.querySelectorAll(".nimc-console__btn");
  const au = new AnsiUp();

  let es = null; // active EventSource
  let runId = null; // id from the server's "started" event
  let stoppable = false; // current action exposes a Stop button

  const setStatus = (state, text) => {
    status.dataset.state = state;
    status.textContent = text;
  };

  const append = (raw) => {
    const span = document.createElement("span");
    span.innerHTML = au.ansi_to_html(raw) + "\n";
    output.appendChild(span);
    output.scrollTop = output.scrollHeight;
  };

  const setRunning = (running) => {
    actionBtns.forEach((b) => (b.disabled = running));
    stopBtn.hidden = !(running && stoppable);
  };

  const finish = (state, text) => {
    if (es) {
      es.close();
      es = null;
    }
    runId = null;
    setRunning(false);
    setStatus(state, text);
  };

  function run(btn) {
    const action = btn.dataset.action;
    if (btn.dataset.confirm && !window.confirm(btn.dataset.confirm)) return;

    if (es) es.close();
    output.hidden = false;
    output.textContent = "";
    runId = null;
    stoppable = btn.dataset.stoppable === "1";
    setRunning(true);
    setStatus("running", `Running ${action}…`);

    es = new EventSource(
      `/console/${encodeURIComponent(slug)}/run/${encodeURIComponent(action)}`,
    );

    es.addEventListener("started", (e) => {
      runId = e.data;
    });

    es.onmessage = (e) => append(e.data.replace(/\\n/g, "\n"));

    es.addEventListener("done", (e) => {
      const code = e.data.replace("exit_code=", "");
      finish(
        code === "0" ? "done" : "error",
        code === "0" ? "Done (exit 0)" : `Exited with code ${code}`,
      );
    });

    es.addEventListener("error", (e) => {
      if (e.data) {
        // Application-level error from the server (e.g. command not found).
        append(`\x1b[31m${e.data}\x1b[0m`);
        finish("error", "Error");
      } else if (es && es.readyState === EventSource.CLOSED) {
        // Transport dropped without a clean "done".
        finish("error", "Disconnected");
      }
    });
  }

  async function stop() {
    if (runId) {
      const headers = {};
      const m = document.cookie.match(/csrf_token=([^;]+)/);
      if (m) headers["X-CSRF-Token"] = m[1];
      try {
        await fetch(
          `/console/${encodeURIComponent(slug)}/stop/${encodeURIComponent(runId)}`,
          { method: "POST", headers },
        );
        // The subprocess dies, the stream emits "done", which cleans up.
        setStatus("running", "Stopping…");
      } catch {
        finish("error", "Stop failed");
      }
    } else {
      // No run_id yet (stopped before it started): just drop the stream.
      finish("idle", "Stopped");
    }
  }

  actionBtns.forEach((b) => b.addEventListener("click", () => run(b)));
  stopBtn.addEventListener("click", stop);
}

document.querySelectorAll(".nimc-console").forEach(initConsole);
