document.addEventListener("DOMContentLoaded", () => {
  const fields = ["cost", "supplier", "supplier_wechat", "shipping_note"];
  const labels = {cost: "单件成本", supplier: "供应商", supplier_wechat: "微信备注", shipping_note: "发货备注"};
  const normalized = values => JSON.stringify(fields.map(name => {
    const value = values[name].trim();
    return name === "cost" && value && Number.isFinite(Number(value)) ? Number(value).toFixed(2) : value;
  }));
  document.querySelectorAll("form.wb-product-card").forEach(form => {
    const button = form.querySelector("[data-save-product]");
    const feedback = form.querySelector(".wb-save-feedback");
    const values = () => Object.fromEntries(fields.map(name => [name, form.elements.namedItem(name).value]));
    let baseline = normalized(values());
    let hasSaved = form.dataset.saved === "true";
    let saving = false;
    let failed = false;
    const render = () => {
      const saved = hasSaved && baseline === normalized(values()) && !failed;
      button.disabled = saving || saved;
      button.textContent = saving ? "保存中…" : saved ? "已保存" : "保存";
      button.classList.toggle("is-saved", !saving && saved);
      button.setAttribute("aria-busy", String(saving));
    };
    const edited = () => {
      feedback.textContent = "";
      render();
    };
    form.addEventListener("input", edited);
    form.addEventListener("change", edited);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (saving || button.disabled || !form.reportValidity()) return;
      const submitted = normalized(values());
      const body = new FormData(form);
      saving = true;
      failed = false;
      feedback.textContent = "";
      render();
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      try {
        const response = await fetch(form.action, {
          method: "POST", body, credentials: "same-origin",
          headers: {Accept: "application/json"}, signal: controller.signal,
        });
        if (response.redirected || response.status === 403) {
          throw new Error("登录或权限已失效，请在新窗口重新登录后重试。当前输入已保留。");
        }
        if (!response.headers.get("Content-Type")?.includes("application/json")) {
          throw new Error("未确认保存成功，请重试。当前输入已保留。");
        }
        const data = await response.json();
        if (!response.ok) {
          const errors = Object.entries(data.errors || {}).flatMap(([name, errors]) =>
            errors.map(error => `${labels[name] || "配置"}：${error.message}`));
          throw new Error(errors.join("；") || "未确认保存成功，请重试。当前输入已保留。");
        }
        if (!data.saved) throw new Error("未确认保存成功，请重试。当前输入已保留。");
        // Changes typed while the request was running remain unsaved and editable.
        if (normalized(values()) === submitted) {
          for (const name of fields) form.elements.namedItem(name).value = data.saved[name];
        }
        baseline = normalized(data.saved);
        hasSaved = true;
        form.querySelector("[data-cost-version]").textContent = data.version;
        form.querySelector("[data-product-condition]").textContent = data.display_condition || "";
        form.querySelector("[data-condition-row]").hidden = !data.display_condition;
        const history = form.querySelector("[data-cost-history]");
        history.replaceChildren(...data.revisions.map(text => {
          const paragraph = document.createElement("p");
          paragraph.textContent = text;
          return paragraph;
        }));
        document.dispatchEvent(new CustomEvent("supplier-choices-updated", {detail: data.supplier_choices}));
      } catch (error) {
        failed = true;
        feedback.textContent = error.name === "AbortError" || error instanceof TypeError
          ? "未确认保存成功，请检查网络后重试。当前输入已保留。" : error.message;
      } finally {
        clearTimeout(timeout);
        saving = false;
        render();
      }
    });
    render();
  });
});
