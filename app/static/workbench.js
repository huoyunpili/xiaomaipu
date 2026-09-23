document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-recovery-url]").forEach(control => {
    const select = control.querySelector("select");
    const button = control.querySelector("[data-save-recovery]");
    const feedback = control.querySelector("[data-recovery-feedback]");
    const render = () => { button.disabled = select.value === control.dataset.recovered; };
    select.addEventListener("change", () => { feedback.textContent = ""; render(); });
    button.addEventListener("click", async () => {
      const selected = select.value;
      button.disabled = true; select.disabled = true; button.textContent = "保存中…";
      feedback.textContent = "";
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 30000);
      try {
        const response = await fetch(control.dataset.recoveryUrl, {
          method: "POST", credentials: "same-origin", signal: controller.signal,
          headers: {"X-CSRFToken": control.querySelector('[name="csrfmiddlewaretoken"]').value, "Accept": "application/json"},
          body: new URLSearchParams({recovered: selected}),
        });
        if (response.redirected || response.status === 403) throw new Error("登录或权限已失效，请重新登录后重试。");
        if (!response.headers.get("Content-Type")?.includes("application/json")) throw new Error("未确认保存成功，请重试。");
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "保存失败，请重试。");
        control.dataset.recovered = data.recovered ? "yes" : "no";
        select.value = control.dataset.recovered;
        const card = control.closest(".wb-order");
        const status = card.querySelector(".wb-recovery");
        status.textContent = data.recovered ? "供应商货款：已追回（已收回）" : "供应商货款：未追回";
        status.classList.toggle("wb-recovery-recovered", data.recovered);
        status.classList.toggle("wb-recovery-unrecovered", !data.recovered);
        const amount = card.querySelector("[data-unrecovered-amount]");
        if (amount) amount.hidden = data.recovered;
        const detailCheckbox = document.getElementById("id_recovered");
        if (detailCheckbox) detailCheckbox.checked = data.recovered;
        document.querySelectorAll("[data-recovery-count]").forEach(node => { node.textContent = data.recovery_count; });
        feedback.textContent = "已保存";
      } catch (error) {
        feedback.textContent = error.name === "AbortError" || error instanceof TypeError
          ? "未确认保存成功，请检查网络后重试。" : error.message;
      } finally {
        clearTimeout(timeout); select.disabled = false; button.textContent = "确定"; render();
      }
    });
    render();
  });
  const accountMenu = document.querySelector('.account-menu');
  if (accountMenu) {
    document.addEventListener('click', event => {
      if (!accountMenu.contains(event.target)) accountMenu.open = false;
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && accountMenu.open) {
        accountMenu.open = false;
        accountMenu.querySelector('summary').focus();
      }
    });
  }
  const select=document.getElementById("select-all");
  if(select) select.addEventListener("change",()=>document.querySelectorAll('[name="selected"]').forEach(c=>c.checked=select.checked));
  document.querySelectorAll(".wb-thumb img").forEach(img=>{
    const unavailable=()=>{const s=document.createElement("span");s.className="wb-image-message";s.textContent="图片暂不可用";img.replaceWith(s);};
    img.addEventListener("error", unavailable, {once:true});
    if(img.complete && !img.naturalWidth) unavailable();
  });
  document.querySelectorAll(".wb-export-form").forEach(form=>form.addEventListener("submit",e=>{
    if(!form.querySelector('[name="selected"]:checked')){e.preventDefault();alert("请先选择订单。");return;}
    const b=form.querySelector("button");b.disabled=true;b.textContent="正在同步并生成，请稍候…";
  }));
  document.querySelectorAll('input[type="date"]').forEach(input=>input.addEventListener("change",()=>{
    const range=input.form.querySelector('[name="range"]');if(range)range.value="custom";
  }));
});
