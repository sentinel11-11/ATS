import React from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

export default function ToastContainer({ toasts, removeToast }) {
  if (!toasts || toasts.length === 0) return null;

  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 max-w-sm pointer-events-none">
      {toasts.map((t) => {
        const isErr = t.type === 'err';
        const isOk = t.type === 'ok';

        return (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-start gap-3 p-3.5 rounded-xl border backdrop-blur-md shadow-2xl text-xs font-medium text-white transition-all animate-in fade-in slide-in-from-right-4 duration-200 ${
              isErr
                ? 'bg-[#1a0f14]/95 border-bad/40 border-l-4 border-l-bad'
                : isOk
                ? 'bg-[#0f1f18]/95 border-ok/40 border-l-4 border-l-ok'
                : 'bg-[#0f1b30]/95 border-acc/40 border-l-4 border-l-acc'
            }`}
          >
            {isErr ? (
              <AlertCircle className="w-4 h-4 text-bad shrink-0 mt-0.5" />
            ) : isOk ? (
              <CheckCircle2 className="w-4 h-4 text-ok shrink-0 mt-0.5" />
            ) : (
              <Info className="w-4 h-4 text-acc2 shrink-0 mt-0.5" />
            )}
            <span className="flex-1 leading-snug">{t.msg}</span>
            <button
              onClick={() => removeToast(t.id)}
              className="text-muted hover:text-white p-0.5 shrink-0"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
