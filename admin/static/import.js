/* The import dialog: drag-and-drop, two formats, and a preview that is
 * confirmed before anything is written.
 *
 * WHAT THIS REPLACES. /admin/static/import-csrf.js, which is deleted with
 * this file's arrival. That script existed because sqladmin's own
 * `statics/js/main.js` builds the upload's `FormData` by hand and never
 * serialises the form:
 *
 *     const formData = new FormData();
 *     formData.append('csvfile', file);
 *     formData.append('continue_on_error', ...);
 *     fetch(frm.attr('action'), { method: ..., body: formData, ... });
 *
 * so a hidden `<input name="csrf_token">` was in the DOM and in no request.
 * WP1 refused to fork those 290 lines and refused to put the token in the URL
 * (it would then be in every deployment's nginx access log, and a token
 * written into logs is one nobody can rotate out of them), and carried the
 * field by replacing `window.fetch` for the duration of one call. It said the
 * replacement dialog would delete the wrapper. This is that dialog, and it
 * builds its own body — the token and the mode are ordinary fields in it,
 * which is what every other form in this panel sends. Nothing here patches
 * anything global.
 *
 * THE SERVER IS STILL THE THING THAT REFUSES A FORGED POST. The check in
 * admin/importing.py has not moved and does not depend on this file; what
 * this file does is make a legitimate import possible from a browser.
 *
 * WHY THERE ARE TWO REQUESTS AND NOT ONE. The first carries `X-Dry-Run: true`
 * and the route answers what the file *would* do — created, updated,
 * deactivated or deleted, rejected and why — without opening a write session
 * at all (admin/importing.py returns before `import_csv` is ever called). The
 * second is the import. They are the same upload, the same mode and the same
 * code path with the write left off, which is what stops a preview and the
 * import it previews describing the file differently.
 *
 * **Upsert is what makes the preview necessary rather than pleasant.** A row
 * whose key already exists is a correction; a row whose key has a typo in it
 * is silently a NEW ROW. Nothing in the result of a successful import
 * distinguishes those two, and the preview is the only place a reader can.
 *
 * EVERY NUMBER SHOWN HERE CAME FROM THE SERVER. This file computes nothing
 * about the file it is sending: it does not parse the CSV, it does not count
 * rows, it does not decide what is a create and what is an update. It renders
 * `build_import_plan`'s own answer. A browser that disagreed with the server
 * about what an upload would do would be the worst of both — a preview that
 * is reassuring and wrong.
 */
(function () {
  "use strict";

  var form = document.getElementById("kaicalc-import-form");
  if (!form) {
    return;
  }

  var fileInput = document.getElementById("kaicalc-import-file");
  var dropZone = document.getElementById("kaicalc-import-drop");
  var fileName = document.getElementById("kaicalc-import-filename");
  var modeSelect = document.getElementById("kaicalc-import-mode");
  var csrfField = document.getElementById("kaicalc-import-csrf");
  var preview = document.getElementById("kaicalc-import-preview");
  var message = document.getElementById("kaicalc-import-message");
  var checkButton = document.getElementById("kaicalc-import-check");
  var confirmButton = document.getElementById("kaicalc-import-confirm");
  var confirmCell = document.getElementById("kaicalc-import-confirm-cell");
  var modal = document.getElementById("modal-import");

  var busy = false;

  /* --- the small helpers, so the flow below reads as the flow ----------- */

  function show(element) { element.classList.remove("d-none"); }
  function hide(element) { element.classList.add("d-none"); }

  function clear(element) {
    while (element.firstChild) {
      element.removeChild(element.firstChild);
    }
  }

  function say(element, text, level) {
    /* textContent, never innerHTML. A refusal from the server quotes the
     * value that was refused, and a value that was refused is a value
     * somebody typed - which on a free-text column such as `source_note` can
     * be anything at all. Writing it as markup would be this panel rendering
     * an uploaded file's contents as HTML. */
    clear(element);
    element.textContent = text;
    element.className = level ? "mt-3 alert " + level : "mt-3";
    show(element);
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  /* The confirmation is spent the moment anything about the upload changes.
   * A preview describes one file in one mode; leaving the button live after
   * either changed would be confirming something the visitor never saw. */
  function forgetThePreview() {
    hide(confirmCell);
    hide(preview);
    clear(preview);
  }

  /* --- choosing a file, by pointer, by keyboard or by dropping it ------- */

  function announceTheFile() {
    var chosen = fileInput.files && fileInput.files[0];
    if (chosen) {
      fileName.textContent = chosen.name;
      fileName.classList.remove("is-empty");
    } else {
      fileName.textContent = fileName.getAttribute("data-empty");
      fileName.classList.add("is-empty");
    }
    hide(message);
    forgetThePreview();
  }

  fileInput.addEventListener("change", announceTheFile);
  modeSelect.addEventListener("change", forgetThePreview);

  /* DRAG AND DROP. `dragover` has to be cancelled or the browser navigates to
   * the file instead of handing it over - that default is the single reason
   * most hand-written drop zones do nothing. `dragleave` fires when the
   * pointer crosses onto a CHILD element as well, so the highlight is counted
   * in and out rather than toggled, otherwise it flickers off the moment the
   * pointer reaches the label in the middle of the zone. */
  var depth = 0;

  function overTheZone(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  dropZone.addEventListener("dragenter", function (event) {
    overTheZone(event);
    depth += 1;
    dropZone.classList.add("is-dragging");
  });

  dropZone.addEventListener("dragover", function (event) {
    overTheZone(event);
    // Some browsers need this said explicitly to draw a copy cursor rather
    // than a "no entry" one.
    if (event.dataTransfer) { event.dataTransfer.dropEffect = "copy"; }
  });

  dropZone.addEventListener("dragleave", function (event) {
    overTheZone(event);
    depth = Math.max(0, depth - 1);
    if (depth === 0) { dropZone.classList.remove("is-dragging"); }
  });

  dropZone.addEventListener("drop", function (event) {
    overTheZone(event);
    depth = 0;
    dropZone.classList.remove("is-dragging");
    var dropped = event.dataTransfer && event.dataTransfer.files;
    if (!dropped || !dropped.length) { return; }
    /* Assigned to the real `<input type="file">` rather than kept in a
     * variable of its own, so that there is exactly one answer to "which file
     * is this dialog about" - the control's own. `DataTransfer.files` is a
     * `FileList` and `input.files` accepts one directly. */
    fileInput.files = dropped;
    announceTheFile();
  });

  /* A file dropped anywhere ELSE on the page is still a file the browser
   * would navigate to, replacing the panel with the contents of a CSV. Only
   * prevented, never handled: dropping on the page at large means nothing
   * here, and silently importing it would be the opposite of a preview. */
  ["dragover", "drop"].forEach(function (name) {
    window.addEventListener(name, function (event) {
      if (!dropZone.contains(event.target)) { event.preventDefault(); }
    });
  });

  /* --- the two requests ------------------------------------------------- */

  function send(dryRun) {
    var body = new FormData();
    body.append("csvfile", fileInput.files[0]);
    body.append("csrf_token", csrfField.value);
    body.append("import_mode", modeSelect.value);

    var headers = {};
    if (dryRun) {
      // §6.2's own word. This panel already has a dry-run vocabulary - the
      // header on POST /api/v1/calculate and the /admin/try screen that sends
      // it - and a second one would be a second thing to learn.
      headers["X-Dry-Run"] = "true";
    }
    return fetch(form.getAttribute("action"), {
      method: "POST",
      body: body,
      headers: headers,
      credentials: "same-origin"
    });
  }

  function working(state, label) {
    busy = state;
    checkButton.disabled = state;
    confirmButton.disabled = state;
    checkButton.textContent = state && label ? label : "Check this file";
  }

  /* --- the preview ------------------------------------------------------ */

  function countPill(list, label, value, className) {
    if (!value) { return; }
    list.appendChild(element("li", className, value + " " + label));
  }

  function rowList(node, heading, rows, describe) {
    if (!rows || !rows.length) { return; }
    node.appendChild(element("p", "kaicalc-import-heading", heading));
    var ul = element("ul", "kaicalc-import-rows");
    rows.forEach(function (row) {
      ul.appendChild(element("li", null, describe(row)));
    });
    node.appendChild(ul);
  }

  function render(plan) {
    clear(preview);
    preview.className = "mt-3 alert " + (plan.ok ? "alert-info" : "alert-danger");

    preview.appendChild(element("p", "mb-0 fw-bold", plan.summary));

    var counts = element("ul", "kaicalc-import-counts");
    countPill(counts, "to add", plan.counts.created, null);
    countPill(counts, "to update", plan.counts.updated, null);
    countPill(counts, "to deactivate", plan.counts.deactivated, "is-retiring");
    countPill(counts, "to delete", plan.counts.deleted, "is-retiring");
    countPill(counts, "rejected", plan.counts.rejected, "is-rejected");
    if (counts.childNodes.length) { preview.appendChild(counts); }

    /* ADDED ROWS FIRST, AND NAMED. On an upsert a row that "would be added"
     * is either a row somebody meant to add or a key with a typo in it, and
     * those look identical in a count. Showing the keys is what lets a reader
     * tell them apart, which is the whole reason this preview exists. */
    rowList(preview, "Would be added", plan.created, function (row) {
      return row.key + "  (line " + row.line + ")";
    });
    rowList(preview, "Would be updated", plan.updated, function (row) {
      return row.key + "  (line " + row.line + ")";
    });
    rowList(preview, "Would be deactivated", plan.deactivated, function (row) {
      return row.key;
    });
    rowList(preview, "Would be deleted", plan.deleted, function (row) {
      return row.key;
    });

    if (plan.rejected && plan.rejected.length) {
      preview.appendChild(element("p", "kaicalc-import-heading", "Refused"));
      preview.appendChild(element(
        "div", "kaicalc-import-rejections", plan.rejected.join("\n\n")
      ));
    }
    if (plan.line_note) {
      preview.appendChild(element("p", "mt-2 mb-0 small", plan.line_note));
    }

    show(preview);
    if (plan.ok) { show(confirmCell); } else { hide(confirmCell); }
  }

  /* --- step one: check the file ---------------------------------------- */

  form.addEventListener("submit", function (event) {
    // Always. The dialog posts through `fetch` so that the preview can be
    // shown in place; letting the browser submit would navigate away from the
    // panel and land on a bare NDJSON stream.
    event.preventDefault();
    if (busy) { return; }

    if (!fileInput.files || !fileInput.files.length) {
      say(message, "Choose a .csv or .json file first.", "alert-warning");
      return;
    }

    hide(message);
    forgetThePreview();
    working(true, "Checking…");

    send(true).then(function (response) {
      if (!response.ok) {
        /* A refusal is plain text from admin/importing.py and names every
         * value it could not read, one per line. Shown as it was written. */
        return response.text().then(function (text) {
          say(message, text || "This file could not be checked.", "alert-danger");
        });
      }
      return response.json().then(render);
    }).catch(function (error) {
      say(message, "The file could not be checked: " + error.message,
          "alert-danger");
    }).then(function () {
      working(false);
    });
  });

  /* --- step two: do it -------------------------------------------------- */

  confirmButton.addEventListener("click", function () {
    if (busy) { return; }
    if (!fileInput.files || !fileInput.files.length) {
      say(message, "Choose a .csv or .json file first.", "alert-warning");
      return;
    }
    hide(message);
    working(true, "Importing…");
    confirmButton.textContent = "Importing…";

    send(false).then(function (response) {
      if (!response.ok) {
        return response.text().then(function (text) {
          say(message, text || "The import was refused.", "alert-danger");
          hide(confirmCell);
        });
      }
      /* sqladmin answers a real import with newline-delimited JSON: one
       * `progress` event per batch and one `result` at the end. Read to the
       * end and report the result rather than the last progress line - the
       * rows are persisted after the final progress event, so a dialog that
       * stopped reading early would claim success before the commit. */
      return response.text().then(function (body) {
        var last = null;
        body.split("\n").forEach(function (line) {
          if (!line.trim()) { return; }
          try {
            var event = JSON.parse(line);
            if (event.type === "result") { last = event; }
          } catch (ignored) { /* a partial line at the end of the stream */ }
        });
        if (last && last.ok) {
          say(message, last.summary + " Reloading the list…", "alert-success");
          hide(confirmCell);
          window.setTimeout(function () { window.location.reload(); }, 900);
        } else {
          say(message,
              (last && last.summary) || "The import did not complete.",
              "alert-danger");
          hide(confirmCell);
        }
      });
    }).catch(function (error) {
      say(message, "The import could not be sent: " + error.message,
          "alert-danger");
    }).then(function () {
      working(false);
      confirmButton.textContent = "Import";
    });
  });

  /* --- opening and closing --------------------------------------------- */

  if (modal) {
    /* A dialog reopened after a refusal must not still be showing it. The
     * file input is cleared too: a second import of the same file is a
     * deliberate act and should be chosen again, and a stale `FileList` after
     * the page's rows have changed is a preview computed against a table that
     * no longer looks like that. */
    modal.addEventListener("hidden.bs.modal", function () {
      if (busy) { return; }
      fileInput.value = "";
      announceTheFile();
      hide(message);
    });
  }
})();
