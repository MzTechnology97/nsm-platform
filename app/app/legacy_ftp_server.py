"""Upload-only FTP receiver for legacy RouterOS backups (``python -m app.legacy_ftp_server``).

A deliberately small asyncio server that speaks just what ``/tool fetch
upload=yes mode=ftp`` needs: USER, PASS, TYPE, PASV/EPSV, STOR, plus harmless
SYST/FEAT/PWD/CWD/NOOP/OPTS/QUIT.  Anything else answers 502.

- accounts are the one-time job accounts of ``legacy_ftp_backup`` (three wrong
  passwords close the connection);
- only the expected file names of that job can be stored, once each, up to
  ``MAX_FILE_BYTES``; no listing, download, delete or rename;
- passive mode only, data connection accepted only from the control
  connection's address, within ``DATA_TIMEOUT``;
- per-address connection limit and idle timeouts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path

from app import legacy_ftp_backup as lfb

log = logging.getLogger("legacy-ftp")
CONTROL_TIMEOUT = 120
DATA_TIMEOUT = 60
MAX_PER_ADDRESS = 4
MAX_AUTH_FAILURES = 3


def _passive_ports() -> list[int]:
    raw = os.getenv("LEGACY_FTP_PASV_PORTS", "30100-30109")
    low, _, high = raw.partition("-")
    return list(range(int(low), int(high or low) + 1))


class Session:
    def __init__(self, server, reader, writer):
        self.server, self.reader, self.writer = server, reader, writer
        self.peer = (writer.get_extra_info("peername") or ("?", 0))[0]
        self.user = None
        self.account = None
        self.failures = 0
        self.passive = None  # (asyncio.Server, Future[(reader, writer)])
        self.stored: set[str] = set()

    async def reply(self, line: str):
        self.writer.write((line + "\r\n").encode())
        await self.writer.drain()

    async def run(self):
        await self.reply("220 NSM backup receiver")
        try:
            while True:
                raw = await asyncio.wait_for(self.reader.readline(), CONTROL_TIMEOUT)
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").strip()
                command, _, argument = line.partition(" ")
                if not await self.handle(command.upper(), argument.strip()):
                    return
        except (asyncio.TimeoutError, ConnectionError):
            return
        finally:
            await self.close_passive()
            self.writer.close()

    async def handle(self, command: str, argument: str) -> bool:
        if command == "USER":
            self.user, self.account = argument[:64], None
            await self.reply("331 Password required")
        elif command == "PASS":
            account = await asyncio.to_thread(lfb.authenticate, self.user or "", argument)
            if account is None:
                self.failures += 1
                await self.reply("530 Login incorrect")
                return self.failures < MAX_AUTH_FAILURES
            self.account = account
            await self.reply("230 Logged in")
        elif command == "QUIT":
            await self.reply("221 Bye")
            return False
        elif command in ("SYST",):
            await self.reply("215 UNIX Type: L8")
        elif command == "FEAT":
            await self.reply("211-Features:\r\n EPSV\r\n PASV\r\n211 End")
        elif command in ("NOOP", "OPTS", "MODE", "STRU"):
            await self.reply("200 OK")
        elif self.account is None:
            await self.reply("530 Please login")
        elif command == "TYPE":
            await self.reply("200 Type set")
        elif command == "PWD":
            await self.reply('257 "/"')
        elif command == "CWD":
            await self.reply("250 OK" if argument in ("", "/", ".") else "550 No such directory")
        elif command in ("PASV", "EPSV"):
            port = await self.open_passive()
            if port is None:
                await self.reply("425 No passive port available")
            elif command == "EPSV":
                await self.reply(f"229 Entering Extended Passive Mode (|||{port}|)")
            else:
                host = lfb.public_host().replace(".", ",")
                await self.reply(f"227 Entering Passive Mode ({host},{port // 256},{port % 256})")
        elif command == "STOR":
            await self.store(argument)
        else:
            await self.reply("502 Command not implemented")
        return True

    async def open_passive(self):
        await self.close_passive()
        for port in self.server.free_ports():
            future = asyncio.get_running_loop().create_future()

            async def accept(reader, writer, future=future):
                peer = (writer.get_extra_info("peername") or ("?", 0))[0]
                if future.done() or peer != self.peer:
                    writer.close()
                    return
                future.set_result((reader, writer))

            try:
                listener = await asyncio.start_server(accept, host="0.0.0.0", port=port)
            except OSError:
                continue
            self.server.busy.add(port)
            self.passive = (listener, future, port)
            return port
        return None

    async def close_passive(self):
        if self.passive:
            listener, future, port = self.passive
            listener.close()
            self.server.busy.discard(port)
            if future.done():
                future.result()[1].close()
            self.passive = None

    async def store(self, argument: str):
        name = Path(argument.replace("\\", "/")).name
        kind = self.account["allowed"].get(name)
        if kind is None or name in self.stored:
            await self.reply("553 File name not allowed")
            return
        if not self.passive:
            await self.reply("425 Use PASV first")
            return
        listener, future, _port = self.passive
        await self.reply("150 Ready to receive")
        temp = lfb.incoming_dir() / f"{uuid.uuid4().hex}.part"
        size = 0
        try:
            reader, writer = await asyncio.wait_for(future, DATA_TIMEOUT)
            with temp.open("wb") as fh:
                while True:
                    chunk = await asyncio.wait_for(reader.read(65536), DATA_TIMEOUT)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > lfb.MAX_FILE_BYTES:
                        raise ValueError("too large")
                    fh.write(chunk)
            writer.close()
        except (asyncio.TimeoutError, ConnectionError, ValueError) as exc:
            temp.unlink(missing_ok=True)
            await self.close_passive()
            await self.reply("552 Transfer aborted" if isinstance(exc, ValueError) else "426 Transfer failed")
            return
        await self.close_passive()
        if size == 0:
            temp.unlink(missing_ok=True)
            await self.reply("552 Empty file")
            return
        await asyncio.to_thread(lfb.register_upload, self.account["job_id"], kind, temp, size)
        self.stored.add(name)
        log.info("stored %s (%d bytes) for job %s from %s", name, size, self.account["job_id"], self.peer)
        await self.reply("226 Transfer complete")


class Server:
    def __init__(self):
        self.ports = _passive_ports()
        self.busy: set[int] = set()
        self.per_address: dict[str, int] = {}

    def free_ports(self):
        return [p for p in self.ports if p not in self.busy]

    async def client(self, reader, writer):
        peer = (writer.get_extra_info("peername") or ("?", 0))[0]
        if self.per_address.get(peer, 0) >= MAX_PER_ADDRESS:
            writer.write(b"421 Too many connections\r\n")
            writer.close()
            return
        self.per_address[peer] = self.per_address.get(peer, 0) + 1
        try:
            await Session(self, reader, writer).run()
        finally:
            self.per_address[peer] -= 1


async def serve(port: int = 2121, bind: str = "0.0.0.0", stop: asyncio.Event | None = None):
    server = Server()
    listener = await asyncio.start_server(server.client, host=bind, port=port)
    log.info("Legacy backup FTP receiver on %s:%s, passive %s", bind, port, server.ports[0])
    async with listener:
        if stop is None:
            await listener.serve_forever()
        else:
            await stop.wait()


def main():
    logging.basicConfig(level=logging.INFO)
    if not lfb.enabled():
        raise SystemExit("LEGACY_FTP_ENABLED=1 e LEGACY_FTP_PUBLIC_HOST=<IPv4 pubblico di NSM> sono richiesti.")
    asyncio.run(serve(int(os.getenv("LEGACY_FTP_LISTEN_PORT", "2121"))))


if __name__ == "__main__":
    main()
