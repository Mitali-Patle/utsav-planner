// Keeps the page where it was when htmx replaces the plan after an edit. Without this,
// the browser briefly loses the old plan, anchors to the Change section below it and
// lands there once the new plan is in.
(() => {
  let top = null;
  document.body.addEventListener("htmx:beforeSwap", (event) => {
    if (event.detail.target && event.detail.target.id === "plan-area") top = window.scrollY;
  });
  document.body.addEventListener("htmx:afterSwap", () => {
    if (top === null) return;
    window.scrollTo({ top, behavior: "instant" });
    top = null;
  });
})();
