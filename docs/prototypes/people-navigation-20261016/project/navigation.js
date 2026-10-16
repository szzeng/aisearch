(() => {
  "use strict";

  const validViews = new Set(["people", "collections", "unnamed", "faces"]);
  const parseView = () => {
    const value = new URL(window.location.href).searchParams.get("preview_view");
    return validViews.has(value) ? value : "people";
  };
  const setPreviewParameter = (view) => {
    const url = new URL(window.location.href);
    url.searchParams.set("preview_view", view);
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  };
  const afterProductBoot = () => {
    const library = document.getElementById("pa-library");
    const heading = library && library.querySelector(".pa-library-heading");
    const headingTitle = heading && heading.querySelector("h2");
    const peopleButton = document.getElementById("pa-people");
    const organizeButton = document.getElementById("pa-organize");
    const collectionsToggle = document.getElementById("pa-collections-toggle");
    const collectionsSection = library && library.querySelector(".pa-collections-section");
    const inbox = document.getElementById("pa-inbox");
    if (!library || !heading || !headingTitle || !peopleButton || !organizeButton || !collectionsToggle || !collectionsSection || !inbox) return;
    if (library.dataset.navigationAdapter === "ready") return;
    const peopleNodes = [
      library.querySelector(".pa-directory-tools"), document.getElementById("pa-list-count"),
      document.getElementById("pa-list-note"), document.getElementById("pa-people-retry"),
      document.getElementById("pa-cards"), document.getElementById("pa-people-sentinel"),
      document.getElementById("pa-more"),
    ];
    if (peopleNodes.some((node) => !node || !library.contains(node))) return;

    const compatibility = document.createElement("div");
    compatibility.className = "pa-nav-compatibility";
    compatibility.hidden = true;
    compatibility.setAttribute("aria-hidden", "true");
    peopleButton.parentNode.insertBefore(compatibility, peopleButton);
    compatibility.append(peopleButton, organizeButton, collectionsToggle);

    const viewTabs = document.createElement("nav");
    viewTabs.className = "pa-preview-tabs";
    viewTabs.setAttribute("aria-label", "人物区域");
    const subTabs = document.createElement("nav");
    subTabs.className = "pa-preview-subtabs";
    subTabs.setAttribute("aria-label", "待整理分类");
    const makeTab = (text, view, container) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.dataset.previewTarget = view;
      button.setAttribute("aria-pressed", "false");
      container.append(button);
      return button;
    };
    makeTab("人物", "people", viewTabs);
    makeTab("合集", "collections", viewTabs);
    makeTab("待整理", "unnamed", viewTabs);
    makeTab("未命名人物", "unnamed", subTabs);
    makeTab("未识别人脸", "faces", subTabs);

    const content = document.createElement("div");
    content.className = "pa-preview-content";
    const peopleRegion = document.createElement("section");
    peopleRegion.className = "pa-preview-people-region";
    peopleRegion.dataset.previewRegion = "people";
    peopleRegion.setAttribute("aria-label", "人物列表");
    peopleNodes.forEach((node) => peopleRegion.append(node));
    collectionsSection.dataset.previewRegion = "collections";
    inbox.dataset.previewRegion = "faces";
    content.append(collectionsSection, peopleRegion, inbox);
    heading.after(viewTabs, subTabs, content, compatibility);
    library.dataset.navigationAdapter = "ready";

    let activeView = parseView();
    const syncPressed = () => {
      viewTabs.querySelectorAll("[data-preview-target]").forEach((button) => {
        const target = button.dataset.previewTarget;
        const selected = activeView === target || (target === "unnamed" && activeView === "faces");
        button.setAttribute("aria-pressed", String(selected));
      });
      subTabs.querySelectorAll("[data-preview-target]").forEach((button) => {
        button.setAttribute("aria-pressed", String(activeView === button.dataset.previewTarget));
      });
      subTabs.hidden = activeView !== "unnamed" && activeView !== "faces";
      headingTitle.textContent = activeView === "collections" ? "人物合集" : activeView === "people" ? "人物" : "待整理";
      library.dataset.previewView = activeView;
    };
    const enterPeopleMode = () => {
      const hiddenToggle = document.getElementById("pa-hidden-toggle");
      const returningFromIgnored = hiddenToggle && hiddenToggle.textContent.includes("返回人物列表");
      if (peopleButton.getAttribute("aria-pressed") !== "true" || returningFromIgnored) peopleButton.click();
    };
    const enterOrganizeMode = () => {
      if (organizeButton.getAttribute("aria-pressed") !== "true") organizeButton.click();
    };
    const enterCollectionsMode = () => {
      const panel = document.getElementById("pa-collections-panel");
      if (panel && panel.hidden) collectionsToggle.click();
    };
    const activate = (view, updateUrl = true) => {
      if (!validViews.has(view)) return;
      if (view === "people") enterPeopleMode();
      else if (view === "collections") enterCollectionsMode();
      else enterOrganizeMode();
      activeView = view;
      syncPressed();
      if (updateUrl) setPreviewParameter(view);
    };
    viewTabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-preview-target]");
      if (button) activate(button.dataset.previewTarget);
    });
    subTabs.addEventListener("click", (event) => {
      const button = event.target.closest("[data-preview-target]");
      if (button) activate(button.dataset.previewTarget);
    });

    syncPressed();
    if (activeView === "people") enterPeopleMode();
    else if (activeView === "collections") enterCollectionsMode();
    else enterOrganizeMode();
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", afterProductBoot, { once: true });
  else afterProductBoot();
})();
