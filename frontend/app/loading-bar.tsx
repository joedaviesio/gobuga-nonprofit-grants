"use client";

/**
 * Reusable indeterminate loading bar.
 * Renders a slim animated bar that slides left-to-right.
 *
 * Props:
 *  - label: optional text shown below the bar
 *  - color: tailwind bg color class (default "bg-blue-500")
 */
export default function LoadingBar({
  label,
  color = "bg-blue-500",
}: {
  label?: string;
  color?: string;
}) {
  return (
    <div className="w-full space-y-2">
      <div className="w-full h-1.5 bg-stone-100 rounded-full overflow-hidden">
        <div
          className={`h-full w-1/3 rounded-full ${color} animate-loading-bar`}
        />
      </div>
      {label && (
        <p className="text-xs text-stone-500">{label}</p>
      )}
    </div>
  );
}
