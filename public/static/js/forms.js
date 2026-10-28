// Clears a field's error as soon as the user changes it, so a fixed field stops looking
// wrong. The server still checks everything when the form is sent again.
(() => {
  const clear = (event) => {
    const input = event.target;
    const field = input.closest(".field-invalid");
    if (field) {
      field.classList.remove("field-invalid");
      input.removeAttribute("aria-invalid");
      const error = field.querySelector(".field-error");
      if (error) error.hidden = true;
    }
    const group = input.closest("fieldset[aria-describedby]");
    const groupError = group && document.getElementById(group.getAttribute("aria-describedby"));
    if (groupError && groupError.classList.contains("field-error")) groupError.hidden = true;
  };
  document.addEventListener("input", clear);
  document.addEventListener("change", clear);
})();
