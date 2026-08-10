/* The dialogs on /admin/security.
 *
 * Plain ES module, no dependencies, no build step, loaded with `defer` from
 * brand/security.html. It is an upgrade over a page that already works: the
 * enrolment step is server-rendered with the `open` attribute, so its QR code
 * and its code field are on screen with this file absent or blocked. The other
 * three dialogs are client-opened, and if this file fails to load their
 * buttons do nothing - acceptable for a staff panel, and the reason those
 * buttons are `type="button"` inside no form, so a failure submits nothing.
 *
 * WHAT THIS FILE DELIBERATELY DOES NOT CONTAIN, and must not grow:
 *
 *   - a focus trap. `showModal()` makes everything outside the dialog inert.
 *   - an Escape handler. `showModal()` gives Escape for free: it fires
 *     `cancel`, then closes.
 *   - a call restoring focus to the button that opened a dialog. The browser
 *     restores focus to whatever was focused when `showModal()` was called.
 *
 * Each of those hand-rolled would be the accessibility regression this project
 * already has on record - a focus call in the wrong place - reintroduced.
 *
 * The one exception is below, and it is the one case the browser cannot do
 * anything about.
 */

const openers = document.querySelectorAll("[data-dialog]");
for (const opener of openers) {
  opener.addEventListener("click", () => {
    const dialog = document.getElementById(opener.dataset.dialog);
    if (dialog && typeof dialog.showModal === "function") {
      dialog.showModal();
    }
  });
}

for (const closer of document.querySelectorAll("[data-close-dialog]")) {
  closer.addEventListener("click", () => {
    const dialog = closer.closest("dialog");
    if (dialog) {
      dialog.close();
    }
  });
}

/* Dialogs the server rendered `open`: the enrolment step, and any dialog a
 * refused submission has to come back inside. They are showing already; this
 * re-opens them modally, which is what gets the backdrop and the inertness.
 *
 * Read the list before touching any of them - `close()` fires a `close` event,
 * and the listener registered further down must not see the one this loop
 * causes. That is why the upgrade and the listener are two loops and not one.
 */
const serverOpened = Array.from(document.querySelectorAll("dialog[open]"));
for (const dialog of serverOpened) {
  if (typeof dialog.showModal !== "function") {
    continue;
  }
  dialog.close();
  dialog.showModal();
}

for (const dialog of serverOpened) {
  /* THE ONE HAND-WRITTEN FOCUS CALL IN THIS FILE, and why it is here.
   *
   * `showModal()` restores focus, on close, to whatever was focused when it
   * was called. These dialogs were opened by the page loading rather than by
   * a person pressing anything, so the thing focused at that moment was the
   * document body, and there is nothing for the browser to restore to: Escape
   * would drop the keyboard back at the top of the document, having lost the
   * place. `data-return-focus` names the control the person would have
   * pressed to get here, and that is where focus goes.
   *
   * The same argument covers the field: with no opener, focus starts wherever
   * the dialog's own order puts it, and for the enrolment dialog the field
   * somebody has come to fill in is the code from their new phone.
   */
  const field = dialog.querySelector("[data-initial-focus]");
  if (field) {
    field.focus();
  }
  const returnTo = document.getElementById(dialog.dataset.returnFocus || "");
  if (returnTo) {
    /* `once` because the argument above is about **this** opening, the one
     * the page performed. Close it and press the button to open it again and
     * the browser now has an opener to restore focus to, so a listener that
     * survived would run alongside the browser's own restoration for every
     * later open. They aim at the same element today and nothing would look
     * wrong - which is exactly why this would be left in place while the
     * comment above it stopped being true. */
    dialog.addEventListener("close", () => returnTo.focus(), { once: true });
  }
}
