/** Icon from the shared NSM sprite (static/ui-icons.svg), same set as the classic interface. */
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return (
    <svg className="ui-icon" width={size} height={size} aria-hidden="true" focusable="false">
      <use href={`/static/ui-icons.svg#i-${name}`} />
    </svg>
  );
}
