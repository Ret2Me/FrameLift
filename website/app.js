(() => {
  'use strict';
  const command = document.querySelector('#command');
  const description = document.querySelector('#mode-description');
  const status = document.querySelector('#copy-status');
  const copy = document.querySelector('#copy-command');
  const modes = {
    quick: 'Pierwsze uruchomienie. Użyj nowej nazwy katalogu sesji.',
    deep: 'Kontynuacja tej samej sesji. Kolejny budżet pracy wynosi około 60 s.',
    full: 'Kontynuacja tej samej sesji aż do ukończenia skończonego banku prób.'
  };
  document.querySelectorAll('input[name="mode"]').forEach(input => {
    input.addEventListener('change', () => {
      if (!input.checked || !Object.hasOwn(modes, input.value)) return;
      const resume = input.value === 'quick' ? '' : ' --resume';
      command.textContent = './target/release/telemetry-yield-rs decode-progressive \\\n  --input /absolute/capture.ogg \\\n  --output /absolute/session \\\n  --mode ' + input.value + ' --threads 4' + resume;
      description.textContent = modes[input.value];
      status.textContent = '';
    });
  });
  copy.addEventListener('click', async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(command.textContent);
      status.textContent = 'Polecenie skopiowane.';
    } catch {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(command);
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = 'Zaznaczono polecenie. Skopiuj je skrótem Ctrl+C lub ⌘C.';
    }
  });
  copy.disabled = false;
  copy.hidden = false;
  document.querySelector('.mode-picker').disabled = false;
})();
