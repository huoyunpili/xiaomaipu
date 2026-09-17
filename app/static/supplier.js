document.addEventListener("DOMContentLoaded", () => {
  const rules = JSON.parse(document.getElementById("courier-rules").textContent);
  let activeUploads = 0;
  window.addEventListener("beforeunload", event => { if(activeUploads){event.preventDefault();event.returnValue="";} });
  function update(card, data) {
    const state = card.querySelector("[data-dispatch-state]");
    state.textContent = data.message; state.dataset.state=data.state;
    state.classList.toggle("success",data.state === "SUCCESS");
    if(data.state && data.state!=="FAILED") {
      card.querySelector('[name=waybill]').value=data.waybill;
      card.querySelector('[name=express_code]').value=data.express_code;
    }
    card.querySelectorAll("[data-shipping] input:not([type=hidden]),[data-shipping] select,[data-shipping] button").forEach(el=>el.disabled=!!data.state && data.state!=="FAILED");
  }
  async function poll(card) {
    try { const response=await fetch(card.dataset.statusUrl,{credentials:"same-origin"});
      if(response.ok) update(card,await response.json());
    } catch { /* Keep the last confirmed state; never manufacture success. */ }
  }
  document.querySelectorAll("[data-order]").forEach(card => {
    const copyRecipient = card.querySelector("[data-copy-recipient]");
    if (copyRecipient) copyRecipient.addEventListener("click", async () => {
      const content = card.querySelector("[data-recipient-text]");
      const result = card.querySelector("[data-copy-result]");
      try {
        await navigator.clipboard.writeText([...content.querySelectorAll("p")].map(p => p.textContent).join("\n"));
        result.textContent = "收货信息已复制";
      } catch {
        const selection = window.getSelection(), range = document.createRange();
        range.selectNodeContents(content); selection.removeAllRanges(); selection.addRange(range);
        result.textContent = "请长按或按 Ctrl+C 复制已选中的收货信息";
      }
    });
    const shipping = card.querySelector("[data-shipping]");
    const waybill = shipping.elements.waybill, carrier = shipping.elements.express_code;
    const hint = document.createElement("p"); hint.className="hint"; hint.setAttribute("role","status");
    carrier.closest("label").after(hint);
    let lastNumber=waybill.value.trim().toUpperCase();
    waybill.addEventListener("input", () => {
      const number=waybill.value.trim().toUpperCase();
      if(number===lastNumber || carrier.disabled)return;
      lastNumber=number; carrier.value="";
      const matches=rules.filter(rule=>new RegExp(rule.pattern).test(number));
      const codes=[...new Set(matches.map(rule=>rule.code))];
      const options=[...carrier.options];
      const available=codes.map(code=>options.find(option=>option.value===code)).filter(Boolean);
      if(codes.length===1 && available.length===1 && matches.every(rule=>rule.auto)) {
        carrier.value=available[0].value;
        hint.textContent=`已按单号规则匹配：${available[0].textContent}，请核对；可手动修改。`;
      } else if(available.length) {
        hint.textContent=`可能是${available.map(option=>option.textContent).join("、")}，请选择实际快递公司。`;
      } else {
        hint.textContent=number ? "暂未匹配到快递公司，请手动选择。" : "";
      }
    });
    carrier.addEventListener("change",()=>{hint.textContent=carrier.value ? "已手动选择快递公司。" : "请选择实际快递公司。";});
    card.querySelector("[data-shipping]").addEventListener("submit", async event => {
      event.preventDefault();const form=event.currentTarget,result=form.querySelector(".result"),button=form.querySelector("button");
      const body=new FormData(form); form.querySelectorAll("input:not([type=hidden]),select,button").forEach(el=>el.disabled=true); result.className="result";result.textContent="正在保存单号…";
      try { const response=await fetch(form.action,{method:"POST",body,credentials:"same-origin"});
        const data=await response.json();if(!response.ok)throw new Error(data.error||"提交失败，请重试。");
        update(card,data);result.textContent="单号已保存；请以平台发货结果为准。";
      } catch(error){result.className="result error";result.textContent=error.message||"网络中断，请刷新核对提交结果。";form.querySelectorAll("input:not([type=hidden]),select,button").forEach(el=>el.disabled=false);}
    });
    card.querySelector("[data-video]").addEventListener("submit", async event => {
      event.preventDefault();const form=event.currentTarget,input=form.querySelector('[type=file]'),button=form.querySelector("button"),result=form.querySelector(".result"),progress=form.querySelector("progress");
      const files=[...input.files];if(!files.length)return;
      if(files.some(file=>file.size>60*1024*1024)){result.className="result error";result.textContent="每个视频不能超过60MB，请重新选择。";return;}
      button.disabled=true;input.disabled=true;activeUploads++;progress.hidden=false;
      try {for(const [index,file] of files.entries()) {
        result.className="result";result.textContent=`正在上传 ${index+1}/${files.length}：${file.name}`;
        const body=new FormData();body.append("video",file);body.append("csrfmiddlewaretoken",form.querySelector('[name=csrfmiddlewaretoken]').value);
        const data=await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open("POST",form.action);xhr.timeout=10*60*1000;
          xhr.upload.onprogress=e=>{if(e.lengthComputable)progress.value=e.loaded/e.total*100;};
          xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);xhr.status===200?resolve(data):reject(new Error(data.error||"保存失败"));}catch{reject(new Error("服务器暂不可用，请稍后重试。"));}};
          xhr.onerror=xhr.ontimeout=()=>reject(new Error("上传中断，请重试；已保存的视频不会重复存储。"));xhr.send(body);});
        const list=card.querySelector("[data-videos]");
        if(!list.querySelector(`[data-sha256="${data.sha256}"]`)) {
          const li=document.createElement("li");li.dataset.sha256=data.sha256;li.textContent=data.name+" · 已保存到店主电脑";list.appendChild(li);
        }
      }result.className="result success";result.textContent="所选视频均已保存，可退出或继续补传。";input.value="";
      }catch(error){result.className="result error";result.textContent=error.message;}finally{activeUploads--;button.disabled=false;input.disabled=false;progress.hidden=true;}
    });
  });
  setInterval(()=>document.querySelectorAll("[data-order]").forEach(card=>{if(["READY","SENDING","UNKNOWN"].includes(card.querySelector("[data-dispatch-state]").dataset.state))poll(card);}),5000);
});
