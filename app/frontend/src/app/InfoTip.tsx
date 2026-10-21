/** A small "i" next to a title that explains a number on hover or keyboard focus (the text is drawn by CSS from data-tip). */
export function InfoTip({ text }: { text: string }) {
  return (
    <span className="ui-info-tip" tabIndex={0} role="img" aria-label={text} data-tip={text}>
      i
    </span>
  );
}
