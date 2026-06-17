"""
rcon_client.py — Async BattlEye RCON client for Arma Reforger

BattlEye RCON uses UDP datagrams with the following packet structure:
  'BE' (0x42 0x45) + 4-byte CRC32 (little-endian) + 0xFF + payload

Payload types:
  Login:            0x00 + password (null-terminated)
  Login response:   0x00 + 0x01 (success) or 0x00 (failure)
  Command:          0x01 + sequence_byte + command_string
  Command response: 0x01 + sequence_byte + response_data
  Server message:   0x02 + sequence_byte + message
  Acknowledge:      0x02 + sequence_byte

Environment variables:
    ARMA_RCON_CONNECT_ADDRESS — RCON host override for the middleware client
    ARMA_RCON_ADDRESS         — RCON host fallback (default 127.0.0.1)
  ARMA_RCON_PORT     — RCON port (default 19999)
  ARMA_RCON_PASSWORD — RCON password (default empty)

Usage:
  client = create_rcon_client()
  await client.connect()
  players = await client.get_players()
  await client.say("Server restarting in 5 minutes")
  await client.disconnect()
"""

import asyncio
import binascii
import logging
import os
import re
import struct
from typing import Optional

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# BattlEye RCON Protocol
# ═══════════════════════════════════════════════════════════

_BE_HEADER = b"BE"
_PACKET_TYPE_LOGIN = 0x00
_PACKET_TYPE_COMMAND = 0x01
_PACKET_TYPE_SERVER_MSG = 0x02
_PAYLOAD_PREFIX = 0xFF

# BattlEye player list line: <id> <ip>:<port> <ping> <guid>(OK|?) <name>
_PLAYER_LINE_RE = re.compile(
    r"^\s*(\d+)\s+"
    r"([\d.]+):\d+\s+"
    r"\d+\s+"
    r"([0-9a-fA-F]+)"
    r"\((?:OK|\?)\)\s+"
    r"(.+?)\s*$",
)


class _BERconProtocol(asyncio.DatagramProtocol):
    """Low-level UDP protocol handler for BattlEye RCON."""

    def __init__(self, client: "BERconClient"):
        self._client = client
        self._transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        """Parse an incoming BattlEye RCON packet and dispatch it."""
        if len(data) < 7:
            log.warning("RCON: received packet too short (%d bytes)", len(data))
            return

        if data[:2] != _BE_HEADER:
            log.warning("RCON: invalid header %r", data[:2])
            return

        received_crc = struct.unpack_from("<I", data, 2)[0]
        payload_with_prefix = data[6:]
        expected_crc = BERconClient._calc_crc32(payload_with_prefix)
        if received_crc != expected_crc:
            log.warning(
                "RCON: CRC32 mismatch (received=%08x expected=%08x)",
                received_crc,
                expected_crc,
            )
            return

        if payload_with_prefix[0] != _PAYLOAD_PREFIX:
            log.warning("RCON: missing 0xFF prefix byte")
            return

        payload = payload_with_prefix[1:]
        if not payload:
            log.debug("RCON: empty payload")
            return

        packet_type = payload[0]

        if packet_type == _PACKET_TYPE_LOGIN:
            self._handle_login_response(payload)
        elif packet_type == _PACKET_TYPE_COMMAND:
            self._handle_command_response(payload)
        elif packet_type == _PACKET_TYPE_SERVER_MSG:
            self._handle_server_message(payload)
        else:
            log.warning("RCON: unknown packet type 0x%02x", packet_type)

    def _handle_login_response(self, payload: bytes) -> None:
        if len(payload) < 2:
            log.warning("RCON: login response too short")
            return

        success = payload[1] == 0x01
        if success:
            log.info("RCON: authentication successful")
            self._client._authenticated = True
        else:
            log.error("RCON: authentication failed")
            self._client._authenticated = False

        login_fut = self._client._login_future
        if login_fut and not login_fut.done():
            login_fut.set_result(success)

    def _handle_command_response(self, payload: bytes) -> None:
        if len(payload) < 2:
            log.warning("RCON: command response too short")
            return

        seq = payload[1]
        response_data = payload[2:].decode("utf-8", errors="replace") if len(payload) > 2 else ""

        future = self._client._pending.pop(seq, None)
        if future and not future.done():
            future.set_result(response_data)
        else:
            log.debug("RCON: unmatched command response seq=%d", seq)

    def _handle_server_message(self, payload: bytes) -> None:
        if len(payload) < 2:
            log.warning("RCON: server message too short")
            return

        seq = payload[1]
        message = payload[2:].decode("utf-8", errors="replace") if len(payload) > 2 else ""
        log.info("RCON server message [seq=%d]: %s", seq, message)

        ack_payload = bytes([_PACKET_TYPE_SERVER_MSG, seq])
        packet = BERconClient._build_packet(ack_payload)
        if self._transport:
            self._transport.sendto(packet)

        if self._client._on_server_message:
            try:
                self._client._on_server_message(message)
            except Exception:
                log.exception("RCON: error in server message callback")

    def error_received(self, exc: Exception) -> None:
        log.error("RCON protocol error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        log.warning("RCON: connection lost: %s", exc)
        self._client._connected = False
        self._client._authenticated = False

        for future in self._client._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("RCON connection lost"))
        self._client._pending.clear()


# ═══════════════════════════════════════════════════════════
# BERconClient
# ═══════════════════════════════════════════════════════════

class BERconClient:
    """Async BattlEye RCON client for Arma Reforger.

    Uses UDP datagrams following the BattlEye RCON protocol.
    All public methods are coroutines suitable for use with asyncio.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 19999,
        password: str = "",
    ):
        self._host = host
        self._port = port
        self._password = password
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[_BERconProtocol] = None
        self._connected = False
        self._authenticated = False
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._keepalive_task: Optional[asyncio.Task] = None
        self._login_future: Optional[asyncio.Future] = None
        self._on_server_message = None

    # ─── Connection ───

    async def connect(self) -> bool:
        """Connect and authenticate with the RCON server.

        Returns True if authentication succeeds, False otherwise.
        """
        loop = asyncio.get_running_loop()

        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _BERconProtocol(self),
                remote_addr=(self._host, self._port),
            )
        except OSError as exc:
            log.error("RCON: failed to create UDP endpoint: %s", exc)
            return False

        self._transport = transport
        self._protocol = protocol
        self._connected = True

        login_payload = bytes([_PACKET_TYPE_LOGIN]) + self._password.encode("utf-8")
        packet = self._build_packet(login_payload)

        self._login_future = loop.create_future()
        self._transport.sendto(packet)

        try:
            success = await asyncio.wait_for(self._login_future, timeout=5.0)
        except asyncio.TimeoutError:
            log.error("RCON: login timed out")
            await self.disconnect()
            return False
        finally:
            self._login_future = None

        if not success:
            log.error("RCON: login rejected by server")
            await self.disconnect()
            return False

        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        log.info("RCON: connected to %s:%d", self._host, self._port)
        return True

    async def disconnect(self) -> None:
        """Close the RCON connection and cancel background tasks."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None

        if self._transport:
            self._transport.close()
            self._transport = None

        self._protocol = None
        self._connected = False
        self._authenticated = False
        self._pending.clear()
        log.info("RCON: disconnected")

    async def _reconnect(self) -> bool:
        """Attempt a single reconnection."""
        log.info("RCON: attempting reconnect to %s:%d", self._host, self._port)
        await self.disconnect()
        return await self.connect()

    # ─── Commands ───

    def _dispatch_command(self, command: str) -> tuple[int, asyncio.Future]:
        """Build, send, and register a command packet. Returns (seq, future)."""
        loop = asyncio.get_running_loop()
        seq = self._next_seq()

        cmd_payload = bytes([_PACKET_TYPE_COMMAND, seq]) + command.encode("utf-8")
        packet = self._build_packet(cmd_payload)

        future = loop.create_future()
        self._pending[seq] = future
        self._transport.sendto(packet)

        return seq, future

    async def send_command(self, command: str, timeout: float = 5.0) -> str:
        """Send an RCON command and wait for the response.

        If the connection is lost, attempts a single reconnect before failing.
        Raises ConnectionError if not connected and reconnect fails.
        Raises asyncio.TimeoutError if the response is not received in time.
        """
        if not self._connected or not self._authenticated:
            if not await self._reconnect():
                raise ConnectionError("RCON: not connected and reconnect failed")

        seq, future = self._dispatch_command(command)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except (asyncio.TimeoutError, ConnectionError):
            self._pending.pop(seq, None)
            log.warning("RCON: command failed (seq=%d): %s — retrying after reconnect", seq, command)
            if await self._reconnect():
                return await self._send_command_once(command, timeout)
            raise

    async def _send_command_once(self, command: str, timeout: float = 5.0) -> str:
        """Send a command without auto-reconnect (used after a reconnect)."""
        if not self._connected or not self._authenticated:
            raise ConnectionError("RCON: not connected")

        seq, future = self._dispatch_command(command)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(seq, None)
            raise

    async def kick_player(self, player_id: int, reason: str = "") -> str:
        """Kick a player by their server-assigned ID."""
        cmd = f"kick {player_id} {reason}".rstrip()
        return await self.send_command(cmd)

    async def ban_player(self, guid: str, duration: int = 0, reason: str = "") -> str:
        """Ban a player by their BattlEye GUID.

        Args:
            guid: The player's BattlEye GUID.
            duration: Ban duration in minutes (0 = permanent).
            reason: Optional ban reason.
        """
        cmd = f"addBan {guid} {duration} {reason}".rstrip()
        return await self.send_command(cmd)

    async def get_players(self) -> list[dict]:
        """Get connected players list. Returns list of dicts with id, name, guid, ip."""
        raw = await self.send_command("players")
        return self._parse_players(raw)

    async def say(self, message: str, player_id: int = -1) -> str:
        """Send a chat message. player_id=-1 broadcasts to all players."""
        return await self.send_command(f"say {player_id} {message}")

    # ─── Background Tasks ───

    async def _keepalive_loop(self) -> None:
        """Send keepalive packets every 25 seconds to maintain the connection."""
        try:
            while self._connected:
                await asyncio.sleep(25)
                if not self._connected or not self._transport:
                    break
                try:
                    seq, _ = self._dispatch_command("")
                    log.debug("RCON: keepalive sent (seq=%d)", seq)
                except Exception:
                    log.warning("RCON: keepalive send failed")
                    break
        except asyncio.CancelledError:
            pass

    # ─── Properties ───

    @property
    def is_connected(self) -> bool:
        """Whether the client is connected and authenticated."""
        return self._connected and self._authenticated

    # ─── Packet Building ───

    @staticmethod
    def _build_packet(payload: bytes) -> bytes:
        """Build a BattlEye RCON packet with header and CRC32.

        Packet structure: 'BE' + CRC32 (4 bytes LE) + 0xFF + payload
        CRC32 is computed over (0xFF + payload).
        """
        data_for_crc = bytes([_PAYLOAD_PREFIX]) + payload
        crc = BERconClient._calc_crc32(data_for_crc)
        return _BE_HEADER + struct.pack("<I", crc) + data_for_crc

    @staticmethod
    def _calc_crc32(data: bytes) -> int:
        """Calculate CRC32 for packet verification (unsigned 32-bit)."""
        return binascii.crc32(data) & 0xFFFFFFFF

    # ─── Internal Helpers ───

    def _next_seq(self) -> int:
        """Get the next sequence number, wrapping at 255."""
        seq = self._seq
        self._seq = (self._seq + 1) % 256
        return seq

    @staticmethod
    def _parse_players(raw: str) -> list[dict]:
        """Parse the RCON 'players' command response into structured dicts."""
        players = []
        if not raw:
            return players

        for line in raw.splitlines():
            m = _PLAYER_LINE_RE.match(line)
            if m:
                players.append({
                    "id": int(m.group(1)),
                    "ip": m.group(2),
                    "guid": m.group(3),
                    "name": m.group(4),
                })

        return players


# ═══════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════

def create_rcon_client() -> BERconClient:
    """Create an RCON client from environment variables.

    Reads:
      ARMA_RCON_CONNECT_ADDRESS (preferred)
      ARMA_RCON_ADDRESS         (fallback, default 127.0.0.1)
      ARMA_RCON_PORT            (default 19999)
      ARMA_RCON_PASSWORD        (default "")
    """
    host = os.getenv(
        "ARMA_RCON_CONNECT_ADDRESS",
        os.getenv("ARMA_RCON_ADDRESS", "127.0.0.1"),
    )
    # 0.0.0.0 is a bind address, not connectable — use loopback instead
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return BERconClient(
        host=host,
        port=int(os.getenv("ARMA_RCON_PORT", "19999")),
        password=os.getenv("ARMA_RCON_PASSWORD", ""),
    )
