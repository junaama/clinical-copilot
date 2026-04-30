import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import './styles/styles.css';

const rootEl = document.getElementById('root');
if (!rootEl) {
  throw new Error('#root element missing — index.html is malformed');
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
