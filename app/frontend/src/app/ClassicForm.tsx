import type { ReactNode } from "react";

import { useSession } from "../api/session";

/**
 * Plain POST to a classic page that ends on another classic page (e.g. the one-shot
 * install command): the browser navigates, the session CSRF token goes in the form.
 */
export function ClassicForm({ action, className, children }: { action: string; className?: string; children: ReactNode }) {
  const csrf = useSession().data?.csrf_token;
  if (!csrf) return null;
  return (
    <form method="post" action={action} className={className ?? "ui-inline-form"}>
      <input type="hidden" name="csrf" value={csrf} />
      {children}
    </form>
  );
}
