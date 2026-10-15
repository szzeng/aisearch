(() => {
  const header = document.querySelector(".app-workspace > .app-header");
  if (!header) return;

  const updateHeight = () => {
    const height = Math.ceil(header.getBoundingClientRect().height);
    document.documentElement.style.setProperty("--app-sticky-header-height", `${height}px`);
  };

  updateHeight();
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(updateHeight);
    observer.observe(header);
  } else {
    window.addEventListener("resize", updateHeight, { passive: true });
    window.addEventListener("orientationchange", updateHeight, { passive: true });
  }
})();
