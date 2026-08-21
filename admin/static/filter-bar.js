/* The "All" box in each multi-select on /admin/submissions.
 *
 * WHAT THIS IS NOT. It is not what makes the All box work. `_ticked` in
 * admin/submission_views.py treats the empty string as "do not narrow", so
 * ticking All produces the unfiltered table whether or not this file loads,
 * and whether or not something else is ticked alongside it. The bar is a
 * `<form method="get">` of native checkboxes inside a `<details>`; all of it
 * works with scripting off, which is the standard the public site's drawer and
 * brand/block_ip.html already hold to.
 *
 * WHAT IT IS. Tidying, and the reason it is worth ten lines: a reader who has
 * ticked Retail and then wants everything back ticks All, and without this
 * they are left looking at two ticked boxes whose meaning they now have to
 * work out. The server resolves that state correctly and the summary above it
 * says "Any stage", but a control that contradicts itself on screen is one the
 * next person does not trust.
 *
 * So: ticking All clears the specific boxes, and ticking a specific box clears
 * All. Exactly the rule the server already applies, made visible at the moment
 * of the click rather than after a round trip.
 */

for (const panel of document.querySelectorAll(".kc-multi-panel")) {
  /* The All box is the one with an empty value — the same marker the server
   * keys on. Matching on the `kc-all` class instead would put the rule in two
   * places, and the class is presentational. */
  const all = panel.querySelector('input[type="checkbox"][value=""]');
  if (!all) continue;

  const specific = [...panel.querySelectorAll('input[type="checkbox"]')].filter(
    (box) => box !== all,
  );

  all.addEventListener("change", () => {
    if (!all.checked) return;
    for (const box of specific) box.checked = false;
  });

  for (const box of specific) {
    box.addEventListener("change", () => {
      if (box.checked) {
        all.checked = false;
      } else if (!specific.some((other) => other.checked)) {
        /* Unticking the last specific box is the same request as ticking All,
         * so it lands in the same state rather than leaving every box empty —
         * which is the display this whole feature exists to stop being
         * ambiguous. */
        all.checked = true;
      }
    });
  }
}
