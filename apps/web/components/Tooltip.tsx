import React from 'react';

type TooltipAlign = 'start' | 'center' | 'end';

type TooltipProps = {
  label: string;
  children: React.ReactNode;
  align?: TooltipAlign;
  className?: string;
};

const alignClasses: Record<TooltipAlign, string> = {
  start: 'left-0',
  center: 'left-1/2 -translate-x-1/2',
  end: 'right-0',
};

export const Tooltip: React.FC<TooltipProps> = ({ label, children, align = 'center', className }) => (
  <span className={`relative inline-flex group ${className ?? ''}`}>
    {children}
    <span
      role="tooltip"
      className={`pointer-events-none absolute top-full z-10 mt-2 whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-[10px] font-semibold text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 ${alignClasses[align]}`}
    >
      {label}
    </span>
  </span>
);
