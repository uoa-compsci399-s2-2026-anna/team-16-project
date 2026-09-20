/* Put this panel's CSRF token into sqladmin's import upload.
 *
 * THE PROBLEM THIS SOLVES, AND WHY IT LOOKS LIKE THIS.
 *
 * Every state-changing form in this panel carries a per-session CSRF token
 * and every view checks it (admin/csrf.py). sqladmin's import modal has no
 * token - `csrf` appears zero times in it - and its own submit handler in
 * `sqladmin/statics/js/main.js` does not serialise the form. It builds the
 * body by hand:
 *
 *     const formData = new FormData();
 *     formData.append('csvfile', file);
 *     formData.append('continue_on_error', ...);
 *     fetch(frm.attr('action'), { method: ..., body: formData, headers: ... });
 *
 * So there is nothing to hook: a hidden field is never read, the `formdata`
 * event never fires (it fires only for `new FormData(form)`), and the headers
 * object is built inline. The three ways to get the token into that request
 * were:
 *
 *   1. fork main.js into this repository - ~290 lines of somebody else's
 *      JavaScript, carried forever, for one field;
 *   2. put the token in the URL - it would then be in the nginx access log of
 *      every deployment, and a token written into logs is one nobody can
 *      rotate out of them;
 *   3. hand sqladmin's own `fetch` call the field on its way past, which is
 *      this file.
 *
 * WHAT IT DOES, PRECISELY. On submit of sqladmin's import form it replaces
 * `window.fetch` with a wrapper, and the wrapper restores the original as its
 * very first act - so exactly one call is ever wrapped, the synchronous one
 * sqladmin makes a few lines later in the same handler. A timeout restores it
 * as well, so a submit that returns early (no file chosen) leaves nothing
 * patched. Nothing here is asynchronous and nothing is left behind.
 *
 * WHY THE LISTENER FIRES FIRST. sqladmin binds its handler to `document`
 * (`$(document).on('submit', '#modal-import-form', ...)`), which runs during
 * bubbling. This one is bound to the form element itself, which is the event
 * target, so it runs before any ancestor's - whatever order the two scripts
 * happened to load in.
 *
 * WHEN THIS FILE GOES AWAY. WP4 of the import plan replaces this modal
 * outright (JSON as well as CSV, and drag-and-drop), and the replacement will
 * build its own request body with the token in it. Delete this file and the
 * template that loads it in the same change; the server-side check in
 * admin/importing.py stays either way, because it is the thing that actually
 * refuses a forged post.
 */
(function () {
  "use strict";

  var form = document.getElementById("modal-import-form");
  var holder = document.getElementById("kaicalc-import-csrf");
  if (!form || !holder) {
    return;
  }

  /* The import mode, which travels the same way for the same reason.
   *
   * The block is rendered outside the dialog - the modal is included, not
   * forked, so there is nowhere inside it to write markup - and moved into
   * the dialog's body here. Hidden until it is moved, so that a page whose
   * JavaScript did not run shows nothing rather than a stray control below
   * the table.
   *
   * WP4 replaces this modal and deletes this file with it.
   */
  var modeBlock = document.getElementById("kaicalc-import-mode-block");
  var mode = document.getElementById("kaicalc-import-mode");
  if (modeBlock && mode) {
    var body = form.querySelector(".modal-body");
    if (body) {
      body.appendChild(modeBlock);
      modeBlock.classList.remove("d-none");
    }
  }

  form.addEventListener("submit", function () {
    var original = window.fetch;
    var restored = false;

    function restore() {
      if (!restored) {
        restored = true;
        window.fetch = original;
      }
    }

    window.fetch = function (input, init) {
      restore();
      if (init && init.body && typeof init.body.append === "function") {
        init.body.append("csrf_token", holder.value);
        if (mode) {
          init.body.append("import_mode", mode.value);
        }
      }
      return original.apply(this, arguments);
    };

    // The safety net: a submit that never reaches `fetch` (sqladmin returns
    // early when no file is chosen) would otherwise leave the wrapper in
    // place until the next one.
    window.setTimeout(restore, 0);
  });
})();
