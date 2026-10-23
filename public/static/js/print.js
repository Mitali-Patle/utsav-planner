// Shows the print button only when JavaScript can open the print dialog (F15).
document.querySelectorAll("[data-print]").forEach((button) => {
  button.hidden = false;
  button.addEventListener("click", () => window.print());
});
