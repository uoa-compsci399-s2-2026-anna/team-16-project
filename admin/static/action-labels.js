/* Places an action button's description card next to the button it explains.
 *
 * WHY THIS IS NOT DONE IN CSS, having been attempted in CSS three times.
 *
 * The card is a descendant of sqladmin's own `<a class="btn">` - the label is
 * the only thing `described()` gets to control - and it has to sit near that
 * button without leaving the screen. Anchoring it to the action row kept it on
 * screen and put it a thousand pixels from its button. Anchoring it to the
 * button put it in the right place and ran it off the right-hand edge.
 * Flipping the last two buttons' cards with `:nth-last-child` fixed the
 * right-hand edge only while the row is a single line: once it wraps, the
 * button at the visual edge is not the DOM-last child, and five of seventy-two
 * measured positions were still wrong between 768px and 1024px.
 *
 * CSS cannot ask where an element ended up. Anchor positioning can, and is not
 * available everywhere this panel has to work. So this measures, clamps and
 * places - which is a handful of lines and needs no breakpoint at all.
 *
 * DEGRADES RATHER THAN BREAKS. The class below is what switches the card to
 * JS placement; with no JavaScript the stylesheet's own rules stand and the
 * card is still revealed, still legible, still on screen - anchored to the
 * page rather than to the button. That is the behaviour this file improves on,
 * not a broken state it rescues.
 */
(function () {
  "use strict";

  var GAP = 8;
  var EDGE = 8;

  document.documentElement.classList.add("js-action-tips");

  function place(button) {
    var card = button.querySelector(".action-item__note");
    if (!card) return;

    // Cleared first so the measurement is of the card's natural size at this
    // viewport, not of wherever it was put the last time.
    card.style.left = "";
    card.style.top = "";

    var b = button.getBoundingClientRect();
    var c = card.getBoundingClientRect();

    // Aligned to the button's leading edge, then pulled back inside the
    // viewport if that would hang it off the end. Both clamps are needed and
    // the order matters: on a screen narrower than the card, the second must
    // win so the card starts at the edge rather than off it.
    var left = Math.min(b.left, window.innerWidth - c.width - EDGE);
    left = Math.max(EDGE, left);

    // Above the button, or below it when there is no room above - the action
    // row sits at the foot of a card, so above is almost always right.
    var top = b.top - c.height - GAP;
    if (top < EDGE) top = b.bottom + GAP;

    card.style.left = left + "px";
    card.style.top = top + "px";
  }

  function clear(button) {
    var card = button.querySelector(".action-item__note");
    if (!card) return;
    card.style.left = "";
    card.style.top = "";
  }

  function buttonFor(node) {
    if (!node || typeof node.closest !== "function") return null;
    var button = node.closest("a.btn, button.btn");
    return button && button.querySelector(".action-item__note") ? button : null;
  }

  // Delegated, and on the capture phase for focus/blur, which do not bubble.
  document.addEventListener("mouseover", function (event) {
    var button = buttonFor(event.target);
    if (button) place(button);
  });
  document.addEventListener("mouseout", function (event) {
    var button = buttonFor(event.target);
    if (button) clear(button);
  });
  document.addEventListener("focus", function (event) {
    var button = buttonFor(event.target);
    if (button) place(button);
  }, true);
  document.addEventListener("blur", function (event) {
    var button = buttonFor(event.target);
    if (button) clear(button);
  }, true);

  // A card placed against the viewport is wrong the moment the viewport moves.
  window.addEventListener("scroll", function () {
    var open = document.querySelector("a.btn:hover .action-item__note, button.btn:hover .action-item__note");
    if (open) place(open.parentElement);
  }, true);
})();
