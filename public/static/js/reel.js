// Runs the home page's celebration pictures and wires their pause button (WCAG 2.2.2).
// Without JavaScript, or with reduced motion set, the pictures stay still.
(() => {
  const reel = document.querySelector("[data-reel]");
  if (!reel || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const button = reel.querySelector(".reel-toggle");
  reel.classList.add("is-running");
  button.hidden = false;
  button.addEventListener("click", () => {
    const paused = reel.classList.toggle("is-paused");
    button.textContent = paused ? "Play pictures" : "Pause pictures";
  });
})();
