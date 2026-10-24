// Applies the saved theme before the page paints, then wires the header switch.
// Auto follows the device; Light and Dark are remembered in this browser only.
(() => {
  const root = document.documentElement;
  const order = ["auto", "light", "dark"];
  const labels = { auto: "Auto", light: "Light", dark: "Dark" };

  const saved = () => {
    try {
      const value = localStorage.getItem("utsav-theme");
      return order.includes(value) ? value : "auto";
    } catch {
      return "auto";
    }
  };

  const apply = (choice) => {
    root.dataset.themeChoice = choice;
    if (choice === "auto") {
      delete root.dataset.theme;
    } else {
      root.dataset.theme = choice;
    }
  };

  apply(saved());

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    const label = button.querySelector(".theme-label");
    const show = (choice) => {
      label.textContent = labels[choice];
      button.setAttribute("aria-label", `Theme: ${labels[choice]}. Change theme`);
    };
    show(saved());
    button.hidden = false;
    button.addEventListener("click", () => {
      const next = order[(order.indexOf(root.dataset.themeChoice || "auto") + 1) % order.length];
      try {
        localStorage.setItem("utsav-theme", next);
      } catch {
        // Storage can be blocked (private windows); the choice then lasts for this page.
      }
      apply(next);
      show(next);
    });
  });
})();
