// Marks the section in view in the dashboard's section menu (aria-current="location"),
// and the sections above it as passed, so the side rail fills like a progress line.
(() => {
  const links = new Map(
    [...document.querySelectorAll(".section-nav a[href^='#']")].map((link) => [
      link.getAttribute("href").slice(1),
      link,
    ]),
  );
  if (!links.size || !("IntersectionObserver" in window)) return;

  const mark = (id) => {
    let passed = true;
    links.forEach((link, key) => {
      if (key === id) {
        passed = false;
        link.setAttribute("aria-current", "location");
      } else {
        link.removeAttribute("aria-current");
      }
      link.classList.toggle("is-past", passed);
    });
  };

  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting);
      if (visible.length) mark(visible[0].target.id);
    },
    { rootMargin: "-20% 0px -70% 0px" },
  );

  const watch = () => {
    observer.disconnect();
    links.forEach((_, id) => {
      const section = document.getElementById(id);
      if (section) observer.observe(section);
    });
  };

  watch();
  // htmx replaces the plan area after edits; watch the new sections.
  document.body.addEventListener("htmx:afterSettle", watch);
})();
