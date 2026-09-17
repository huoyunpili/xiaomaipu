/* Render only local immutable text snapshots. No remote image/network dependency. */
document.addEventListener("DOMContentLoaded", async () => {
  const data = JSON.parse(document.getElementById("sheet-data").textContent);
  const controls = document.getElementById("sheet-controls");
  const status = document.getElementById("sheet-status");
  const kind = controls.dataset.kind;
  await document.fonts.ready;
  const measure = document.createElement("canvas").getContext("2d");
  measure.font = '24px "Microsoft YaHei",system-ui,sans-serif';
  const wrap = text => {
    const lines = []; let line = "";
    for (const ch of String(text)) {
      if (ch === "\n" || measure.measureText(line + ch).width > 830) {lines.push(line); line = ch === "\n" ? "" : ch;}
      else line += ch;
    }
    if(line) lines.push(line);
    return lines;
  };
  const money = v => v === null ? "待补成本" : (v / 100).toFixed(2);
  const totalQuantity = data.reduce((sum, row) => sum + row.quantity, 0);
  const groups = []; let current = [], height = 150;
  for (const row of data) {
    const product = [
      row.title ? "商品：" + row.title : "商品名称缺失，请重新生成清单",
      row.spec ? "规格：" + row.spec : "",
      row.condition && !(row.spec || "").includes(row.condition) ? "成色：" + row.condition : "",
      "数量：" + row.quantity + " 件"
    ];
    const values = kind === "shipping"
      ? [...product, "收件人：" + row.receiver + "   电话：" + row.phone, "地址：" + row.address, row.note ? "备注：" + row.note : ""]
      : [...product, row.receiver + " · 手机尾号 " + row.phone.slice(-4), "状态：" + row.status + "   退款 ¥" + money(row.refund_amount_fen ?? row.paid_fen), "成本快照 / 供应商待退成本 ¥" + money(row.cost_fen), "退货要求：" + (row.return_required || "待核对"), "退货单号：" + (row.waybill || "暂无"), "申请时间：" + (row.refund_applied_at || "待核对") + " · 已等待 " + (row.refund_wait_days == null ? "待核对" : row.refund_wait_days + " 天"), row.refund_note];
    const lines = values.filter(Boolean).flatMap(wrap);
    const size = lines.length * 38 + 40;
    if (height + size > 14000 && current.length) {groups.push(current); current = []; height = 150;}
    current.push({lines, size}); height += size;
  }
  if(current.length) groups.push(current);
  const results = await Promise.allSettled(groups.map(async (rows, index) => {
    const buttonSlot = document.createElement("span"), imageSlot = document.createElement("div");
    controls.appendChild(buttonSlot);
    document.getElementById("sheet-images").appendChild(imageSlot);
    const canvas = document.createElement("canvas"); canvas.width = 920;
    canvas.height = 210 + rows.reduce((sum,row) => sum + row.size, 0);
    const ctx = canvas.getContext("2d"); ctx.fillStyle = "#fff"; ctx.fillRect(0,0,canvas.width,canvas.height);
    ctx.fillStyle = "#163f36"; ctx.font = 'bold 28px "Microsoft YaHei",system-ui,sans-serif';
    ctx.fillText((kind === "shipping" ? "发货单" : "退款清单") + " | " + controls.dataset.supplier, 40, 50, 840);
    ctx.font = '20px "Microsoft YaHei",system-ui,sans-serif';
    ctx.fillText(controls.dataset.date + " · 共 " + data.length + " 单 " + totalQuantity + " 件 · " + (index+1) + "/" + groups.length, 40, 88);
    let y = 140;
    for(const row of rows) {
      ctx.font = '24px "Microsoft YaHei",system-ui,sans-serif'; ctx.fillStyle="#182c28";
      for(const line of row.lines) {ctx.fillText(line,40,y); y += 38;}
      ctx.strokeStyle="#dce5df"; ctx.beginPath(); ctx.moveTo(40,y); ctx.lineTo(880,y); ctx.stroke(); y += 40;
    }
    if (kind === "shipping") {
      ctx.font = '20px "Microsoft YaHei",system-ui,sans-serif';
      ctx.fillText("请按收件人姓名和手机号后四位回复快递单号。", 40, y + 10);
    }
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("图片生成失败");
    const form = new FormData();
    form.append("page", String(index + 1));
    form.append("image", blob, "sheet.png");
    form.append("csrfmiddlewaretoken", document.querySelector('#sheet-token [name="csrfmiddlewaretoken"]').value);
    const response = await fetch(controls.dataset.saveUrl, {method: "POST", body: form, credentials: "same-origin"});
    if (!response.ok) throw new Error("私有图片保存失败");
    const saved = await response.json();
    const stored = await fetch(saved.url, {credentials: "same-origin"});
    if (!stored.ok) throw new Error("私有图片读取失败");
    const url=URL.createObjectURL(await stored.blob()), link=document.createElement("a"), img=document.createElement("img");
      link.href=saved.url; link.download=(kind === "shipping" ? "发货单" : "退款清单") + "-" + (index+1) + ".png";
      link.className="button"; link.textContent="下载 PNG · 第 " + (index+1) + " 张"; buttonSlot.appendChild(link);
      img.src=url; img.alt="清单预览"; img.style.cssText="display:block;max-width:100%;margin:20px 0;border:1px solid #ddd";
      imageSlot.appendChild(img);
  }));
  status.textContent=results.some(result => result.status === "rejected")
    ? "部分图片保存失败，请重新打开清单重试；原批次记录已保留。"
    : "图片已按供应商生成并保存到本机私有目录；较长清单自动分张。";
});
