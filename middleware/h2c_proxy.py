"""
Tiny TCP reverse-proxy that strips Enfusion h2c upgrade headers.

Enfusion's REST engine sends "Connection: Upgrade" + "Upgrade: h2c" on every
HTTP request.  Uvicorn (both h11 and httptools) raises HttpParserUpgrade and
abandons the body.  TCP may split the body across multiple data_received()
calls, making a uvicorn monkey-patch unreliable.

This proxy sits on the public port and:
  1. Reads each HTTP request's header block
  2. Strips Upgrade / HTTP2-Settings headers, rewrites Connection
  3. Forwards the sanitized request + body to uvicorn on 127.0.0.1

Usage (called from server.py __main__):
    from h2c_proxy import start_proxy
    start_proxy(listen_port=8080, forward_port=8081)
"""

import asyncio
import logging
import threading

log = logging.getLogger("godmode.h2c_proxy")


async def _pipe(reader, writer):
    """Bidirectional byte pipe: read→write until EOF."""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            if not writer.is_closing():
                writer.close()
        except Exception:
            pass


async def _handle(client_r, client_w, forward_port):
    """Handle one Enfusion connection: strip h2c headers, forward."""
    uvi_w = None
    try:
        # ── Read until end-of-headers (\r\n\r\n) ──
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = await asyncio.wait_for(client_r.read(65536), timeout=15)
            if not chunk:
                return
            buf.extend(chunk)

        hdr_end = buf.index(b"\r\n\r\n") + 4
        header_block = bytes(buf[:hdr_end])
        leftover = bytes(buf[hdr_end:])

        # ── Rewrite headers ──
        lines = header_block.split(b"\r\n")
        out_lines = []
        for line in lines:
            low = line.lower().lstrip()
            if low.startswith(b"upgrade:"):
                continue          # drop Upgrade: h2c
            if low.startswith(b"http2-settings:"):
                continue          # drop HTTP2-Settings
            if low.startswith(b"connection:"):
                out_lines.append(b"Connection: keep-alive")
                continue          # rewrite Connection
            out_lines.append(line)
        sanitized = b"\r\n".join(out_lines)

        # ── Forward to uvicorn ──
        uvi_r, uvi_w = await asyncio.open_connection("127.0.0.1", forward_port)
        uvi_w.write(sanitized)
        if leftover:
            uvi_w.write(leftover)
        await uvi_w.drain()

        # ── Bidirectional pipe for remaining body + response ──
        await asyncio.gather(
            _pipe(client_r, uvi_w),
            _pipe(uvi_r, client_w),
        )
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        for w in (client_w, uvi_w):
            try:
                if w and not w.is_closing():
                    w.close()
            except Exception:
                pass


async def _serve(listen_port, forward_port):
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, forward_port),
        "0.0.0.0",
        listen_port,
    )
    log.info("h2c-strip proxy listening on 0.0.0.0:%d -> 127.0.0.1:%d",
             listen_port, forward_port)
    async with server:
        await server.serve_forever()


def start_proxy(listen_port=8080, forward_port=8081):
    """Start the h2c-stripping proxy in a daemon thread. Returns immediately."""
    def _run():
        asyncio.run(_serve(listen_port, forward_port))

    t = threading.Thread(target=_run, daemon=True, name="h2c-proxy")
    t.start()
    # Give the proxy a moment to bind
    import time
    time.sleep(0.3)
    return t
