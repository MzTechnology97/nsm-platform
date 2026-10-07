import argparse,getpass,sys
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User
from app.security import hash_password,validate_password_strength

def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest='cmd',required=True); c=s.add_parser('create-admin'); c.add_argument('--username'); s.add_parser('rotate-secrets'); a=p.parse_args()
    if a.cmd=='rotate-secrets':
        return rotate_secrets()
    if a.cmd=='create-admin':
        u=(a.username or input('Username amministratore: ')).strip(); pw=getpass.getpass('Password: '); cf=getpass.getpass('Conferma password: ')
        if pw!=cf: print('Le password non coincidono.',file=sys.stderr); return 2
        try: validate_password_strength(pw)
        except ValueError as e: print(e,file=sys.stderr); return 2
        with SessionLocal() as db:
            if db.scalar(select(User).where(User.username==u)): print('Utente già esistente.',file=sys.stderr); return 3
            db.add(User(username=u,password_hash=hash_password(pw),role='admin',is_active=True)); db.commit(); print(f"Amministratore '{u}' creato.")
    return 0


def rotate_secrets():
    """Re-encrypt every stored secret with the current ENCRYPTION_MASTER_KEY."""
    from app.main import add_event
    from app.secret_rotation import inventory, rotate

    with SessionLocal() as db:
        stats = rotate(db)
        add_event(db, "SECRETS_REENCRYPTED", details=stats, source="cli", severity="warning" if stats["unreadable"] else "info")
        db.commit()
        left = inventory(db)["totals"]
    print(f"Segreti ricifrati: {stats['rotated']}; già sulla chiave attuale: {stats['current']}; illeggibili: {stats['unreadable']}.")
    if stats["unreadable"]:
        print("ATTENZIONE: alcuni segreti non sono leggibili con la chiave attuale né con ENCRYPTION_PREVIOUS_KEYS.", file=sys.stderr)
        return 1
    if not left["previous"]:
        print("Tutti i segreti usano la chiave attuale: ENCRYPTION_PREVIOUS_KEYS può essere rimossa.")
    return 0


if __name__=='__main__': raise SystemExit(main())
