import logging,time,json
from redis import Redis
from app.config import settings
logging.basicConfig(level=logging.INFO); log=logging.getLogger('worker'); r=Redis.from_url(settings.redis_url,decode_responses=True)
log.info('Worker avviato')
while True:
    try:
        item=r.blpop('nsp:jobs:default',timeout=5)
        if item:
            job=json.loads(item[1]); log.info('Job ricevuto type=%s id=%s',job.get('type'),job.get('id'))
    except Exception: log.exception('Errore worker'); time.sleep(5)
