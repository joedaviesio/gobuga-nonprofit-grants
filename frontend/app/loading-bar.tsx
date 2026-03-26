"use client";

export default function LoadingBar({
  label,
  color,
}: {
  label?: string;
  color?: string;
}) {
  return (
    <div className="w-full space-y-2">
      <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div
          className={`h-full w-1/3 rounded-full animate-loading-bar ${color || "brand-gradient"}`}
        />
      </div>
      {label && (
        <p className="text-xs text-slate-700">{label}</p>
      )}
    </div>
  );
}
