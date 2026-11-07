document.querySelectorAll('[data-filter]').forEach(input => {
  input.addEventListener('input', () => {
    const table = document.getElementById(input.dataset.filter);
    table.querySelectorAll('tbody tr').forEach(row => {
      row.hidden = !row.textContent.toLowerCase().includes(input.value.toLowerCase());
    });
  });
});
document.querySelectorAll('[data-print]').forEach(button => button.addEventListener('click', () => window.print()));
if (location.hash) {
  const target = document.getElementById(location.hash.slice(1));
  if (target && target.tagName === 'DETAILS') target.open = true;
}
