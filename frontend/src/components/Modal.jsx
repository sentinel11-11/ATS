import React from 'react';
import { X } from 'lucide-react';

export default function Modal({ isOpen, onClose, title, children, wide = false }) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-[#040911]/70 backdrop-blur-md overflow-y-auto animate-in fade-in duration-200">
      <div
        className={`w-full bg-gradient-to-b from-[#10223c] to-[#0b1830] border border-[#2b4c73] rounded-2xl p-6 shadow-2xl relative my-auto animate-in zoom-in-95 duration-200 ${
          wide ? 'max-w-4xl' : 'max-w-2xl'
        }`}
      >
        <div className="flex items-center justify-between pb-4 mb-4 border-b border-line">
          <h2 className="text-lg font-bold text-white flex items-center gap-2">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-muted hover:text-white hover:bg-white/10 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div>{children}</div>
      </div>
    </div>
  );
}
