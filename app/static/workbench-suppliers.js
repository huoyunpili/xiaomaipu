document.addEventListener("DOMContentLoaded", () => {
  const data = document.getElementById("saved-supplier-contacts");
  if (!data) return;
  let suppliers = JSON.parse(data.textContent);
  const contacts = new Map(suppliers.map(item => [item.name, item.wechat]));
  const popup = document.createElement("div");
  popup.id = "wb-supplier-options";
  popup.className = "wb-supplier-popup";
  popup.setAttribute("role", "listbox");
  popup.setAttribute("aria-label", "供应商选项");
  popup.hidden = true;
  document.body.appendChild(popup);
  // Keep the native input usable if the enhanced component stylesheet is unavailable.
  if (getComputedStyle(popup).position !== "fixed") {
    popup.remove();
    return;
  }
  let active = null, options = [], highlighted = -1, showAll = true;
  const close = () => {
    if (active) {
      active.input.setAttribute("aria-expanded", "false");
      active.input.removeAttribute("aria-activedescendant");
      active.toggle.setAttribute("aria-expanded", "false");
    }
    popup.hidden = true;
    active = null;
  };
  const position = () => {
    if (!active || popup.hidden) return;
    const rect = active.wrapper.getBoundingClientRect();
    const viewport = window.visualViewport;
    const topEdge = viewport ? viewport.offsetTop : 0;
    const bottomEdge = topEdge + (viewport ? viewport.height : innerHeight);
    const width = Math.min(Math.max(rect.width, 260), innerWidth - 24);
    const below = bottomEdge - rect.bottom - 12;
    const above = rect.top - topEdge - 12;
    const up = below < Math.min(popup.scrollHeight, 240) && above > below;
    popup.style.width = `${width}px`;
    popup.style.left = `${Math.max(12, Math.min(rect.left, innerWidth - width - 12))}px`;
    popup.style.maxHeight = `${Math.min(280, Math.max(64, up ? above : below))}px`;
    popup.style.top = `${up ? Math.max(topEdge + 8, rect.top - popup.offsetHeight - 6) : rect.bottom + 6}px`;
  };
  const paintHighlight = () => {
    [...popup.querySelectorAll('[role="option"]')].forEach((row, index) => {
      const selected = index === highlighted;
      row.setAttribute("aria-selected", String(selected));
      if (selected) {
        active.input.setAttribute("aria-activedescendant", row.id);
        row.scrollIntoView({block: "nearest"});
      }
    });
    if (highlighted < 0) active.input.removeAttribute("aria-activedescendant");
  };
  const render = () => {
    if (!active) return;
    const query = active.input.value.trim();
    const needle = query.toLocaleLowerCase();
    options = suppliers.filter(item => showAll || `${item.name} ${item.wechat}`.toLocaleLowerCase().includes(needle));
    if (query && !contacts.has(query)) options.push({name: query, wechat: "", isNew: true});
    highlighted = -1;
    popup.replaceChildren();
    if (!options.length) {
      const empty = document.createElement("div");
      empty.className = "wb-supplier-empty";
      empty.textContent = "暂无供应商，输入名称并保存后即可复用。";
      popup.appendChild(empty);
    }
    options.forEach((item, index) => {
      const row = document.createElement("div");
      row.id = `wb-supplier-option-${index}`;
      row.className = "wb-supplier-option";
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", "false");
      const name = document.createElement("strong");
      name.textContent = item.isNew ? `使用新供应商「${item.name}」` : item.name;
      const note = document.createElement("small");
      note.textContent = item.isNew ? "保存商品配置后加入列表" : item.wechat ? `微信备注：${item.wechat}` : "未设置微信备注";
      row.append(name, note);
      if (!item.isNew && item.name === query) {
        const selected = document.createElement("span");
        selected.className = "wb-supplier-selected";
        selected.textContent = "✓";
        selected.setAttribute("aria-hidden", "true");
        row.appendChild(selected);
      }
      row.addEventListener("pointerdown", event => { if (event.pointerType === "mouse") event.preventDefault(); });
      row.addEventListener("click", () => choose(index));
      popup.appendChild(row);
    });
    popup.hidden = false;
    active.input.setAttribute("aria-expanded", "true");
    active.input.removeAttribute("aria-activedescendant");
    active.toggle.setAttribute("aria-expanded", "true");
    position();
  };
  const open = (state, all = true) => {
    if (active !== state) close();
    active = state;
    showAll = all;
    render();
  };
  const choose = index => {
    const state = active, item = options[index];
    if (!state || !item) return;
    state.input.value = item.name;
    state.input.dispatchEvent(new Event("input", {bubbles: true}));
    state.input.dispatchEvent(new Event("change", {bubbles: true}));
    state.input.focus({preventScroll: true});
    close();
  };
  document.querySelectorAll('input[name="supplier"][list="saved-suppliers"]').forEach(input => {
    const wechat = input.form.querySelector('input[name="supplier_wechat"]');
    if (!wechat) return;
    input.removeAttribute("list");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", popup.id);
    input.placeholder = "选择或搜索供应商";
    const wrapper = document.createElement("span");
    wrapper.className = "wb-supplier-combobox";
    input.before(wrapper);
    wrapper.appendChild(input);
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "wb-supplier-toggle";
    toggle.tabIndex = -1;
    toggle.setAttribute("aria-label", "展开供应商列表");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", popup.id);
    const chevron = document.createElement("span");
    chevron.setAttribute("aria-hidden", "true");
    toggle.appendChild(chevron);
    wrapper.appendChild(toggle);
    const state = {input, wrapper, toggle};
    let previousName = input.value.trim(), autoFilled = null;
    input.addEventListener("focus", () => open(state));
    input.addEventListener("click", () => { if (active !== state) open(state); });
    toggle.addEventListener("pointerdown", event => event.preventDefault());
    toggle.addEventListener("click", event => {
      event.preventDefault();
      if (active === state) close();
      else { input.focus({preventScroll: true}); open(state); }
    });
    input.addEventListener("input", () => {
      const name = input.value.trim();
      if (name !== previousName) {
        previousName = name;
        if (contacts.has(name)) { wechat.value = contacts.get(name); autoFilled = wechat.value; }
        else if (autoFilled !== null && wechat.value === autoFilled) { wechat.value = ""; autoFilled = null; }
      }
      open(state, false);
    });
    input.addEventListener("keydown", event => {
      if (event.isComposing) return;
      if (event.key === "Escape" || event.key === "Tab") {
        if (active === state) { if (event.key === "Escape") event.preventDefault(); close(); }
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (active !== state) open(state);
        if (!options.length) return;
        highlighted = event.key === "ArrowDown" ? Math.min(highlighted + 1, options.length - 1)
          : highlighted < 0 ? options.length - 1 : Math.max(0, highlighted - 1);
        paintHighlight();
      } else if (event.key === "Enter" && active === state) {
        event.preventDefault();
        if (highlighted >= 0) choose(highlighted); else close();
      }
    });
  });
  document.addEventListener("pointerdown", event => {
    if (active && !active.wrapper.contains(event.target) && !popup.contains(event.target)) close();
  });
  document.addEventListener("focusin", event => {
    if (active && !active.wrapper.contains(event.target) && !popup.contains(event.target)) close();
  });
  window.addEventListener("scroll", position, true);
  window.addEventListener("resize", position);
  window.visualViewport?.addEventListener("resize", position);
  document.addEventListener("supplier-choices-updated", event => {
    suppliers = event.detail;
    contacts.clear();
    const list = document.getElementById("saved-suppliers");
    list.replaceChildren();
    for (const item of suppliers) {
      contacts.set(item.name, item.wechat);
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.wechat;
      list.appendChild(option);
    }
    if (active) render();
  });
});
