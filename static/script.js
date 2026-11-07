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
document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const field = document.getElementById(button.dataset.copy);
    const status = document.querySelector('[data-copy-status]');
    try {
      await navigator.clipboard.writeText(field.value);
      status.textContent = 'Prompt copied. Paste it into your chosen Claude workspace.';
    } catch {
      field.focus();
      field.select();
      status.textContent = 'Prompt selected. Copy it with Ctrl+C or Command+C.';
    }
  });
});
