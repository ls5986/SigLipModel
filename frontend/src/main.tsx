import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import EvidenceReview from './review/EvidenceReview';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><BrowserRouter><Routes>
    <Route path="/" element={<EvidenceReview />} />
    <Route path="/property-review" element={<EvidenceReview />} />
    <Route path="*" element={<Navigate to="/property-review" replace />} />
  </Routes></BrowserRouter></React.StrictMode>,
);
