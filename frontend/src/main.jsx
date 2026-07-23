import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import AccessGate from './components/AccessGate.jsx';
import './styles/globals.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <AccessGate>
        <App />
      </AccessGate>
    </ErrorBoundary>
  </React.StrictMode>
);
