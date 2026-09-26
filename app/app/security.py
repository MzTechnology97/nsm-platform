import hmac,re,secrets
from fastapi import HTTPException,status
from pwdlib import PasswordHash
password_hash=PasswordHash.recommended()
def hash_password(p): return password_hash.hash(p)
def verify_password(p,h): return password_hash.verify(p,h)
def validate_password_strength(p):
    if len(p)<12: raise ValueError('La password deve contenere almeno 12 caratteri.')
    if not re.search(r'[A-Z]',p) or not re.search(r'[a-z]',p) or not re.search(r'\d',p): raise ValueError('Usa almeno una maiuscola, una minuscola e un numero.')
def csrf_token(request):
    t=request.session.get('csrf_token')
    if not t: t=secrets.token_urlsafe(32); request.session['csrf_token']=t
    return t
def validate_csrf(request,submitted):
    expected=request.session.get('csrf_token')
    if not expected or not submitted or not hmac.compare_digest(expected,submitted): raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail='CSRF token non valido.')
