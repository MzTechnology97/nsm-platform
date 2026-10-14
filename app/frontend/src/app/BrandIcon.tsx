/** Manufacturer of a device, as sent by the UI API (same sprite and colours as the classic pages). */
export interface BrandRef {
  symbol: string;
  color: string | null;
  label: string;
  known: boolean;
}

/**
 * Vendor icon shown next to every device (lists, headers, tables).
 * The colour is an SVG ``fill`` attribute, not a style: allowed by the CSP.
 */
export function BrandIcon({ brand, size = 20, small = false }: { brand?: BrandRef | null; size?: number; small?: boolean }) {
  if (!brand) return null;
  return (
    <span className={`ui-brand-icon${brand.known ? "" : " is-generic"}${small ? " is-small" : ""}`} title={brand.label}>
      <svg width={size} height={size} viewBox="0 0 24 24" fill={brand.color ?? undefined} aria-hidden="true">
        <use href={`/static/brand-icons.svg#${brand.symbol}`} />
      </svg>
      <span className="ui-visually-hidden">{brand.label}</span>
    </span>
  );
}
