document.addEventListener("DOMContentLoaded",()=>{
  const button=document.querySelector("[data-copy-shipping]");if(!button)return;
  button.addEventListener("click",async()=>{const text=document.getElementById("shipping-text");
    try{await navigator.clipboard.writeText(text.value);button.textContent="已复制整份清单";}
    catch{text.focus();text.select();button.textContent="已选中文字，请复制";}
  });
});
