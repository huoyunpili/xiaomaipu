const stage = document.getElementById('stage');
const detail = document.getElementById('detail');
window.fishDesktop.onStatus(status => {
  stage.textContent = status.stage;
  detail.textContent = status.detail;
  document.getElementById('actions').hidden = status.type !== 'error';
  document.getElementById('spinner').hidden = status.type === 'error';
});
for (const action of ['retry', 'diagnostics', 'quit']) {
  document.getElementById(action).addEventListener('click', () => window.fishDesktop[action]());
}
