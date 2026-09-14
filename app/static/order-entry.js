(() => {
  const quantity = document.getElementById('id_quantity');
  const price = document.getElementById('id_unit_price');
  const total = document.getElementById('sale-total');
  if (!quantity || !price || !total) return;
  function update() {
    const match = /^(\d{1,11})(?:\.(\d{0,2}))?$/.exec(price.value);
    if (!/^\d{1,7}$/.test(quantity.value) || !match || BigInt(quantity.value) < 1n) {
      total.textContent = '待填写数量和单价';
      return;
    }
    const cents = (BigInt(match[1]) * 100n + BigInt((match[2] || '').padEnd(2, '0'))) * BigInt(quantity.value);
    total.textContent = `¥${cents / 100n}.${(cents % 100n).toString().padStart(2, '0')}`;
  }
  quantity.addEventListener('input', update);
  price.addEventListener('input', update);
  update();
})();
