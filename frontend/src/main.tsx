import React from 'react';
import { createRoot } from 'react-dom/client';
import EvidenceReview from './review/EvidenceReview';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><EvidenceReview /></React.StrictMode>,
);
