import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import '@/shared/styles/tokens.css';
import '@/shared/styles/global.css';
import { App } from '@/app/App';
import '@alfalab/core-components-themes/corp.css';

const root = document.getElementById('root');

if (!root) {
  throw new Error('Не найден корневой элемент #root');
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
