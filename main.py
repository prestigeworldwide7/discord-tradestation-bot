"""Discord TradeStation Bot

This module implements a small Discord client that listens for trade alerts in
a specified channel and submits bracket orders to the TradeStation API.  It is
meant as a starting point for building an automated trading system: you should
audit the code, adjust it to your needs, and test thoroughly in the simulator
before trading live capital.

The bot expects messages of the form:

    SYMBOL - $STRIKE CALLS/PUTS EXPIRATION MM/DD[/YY] $ENTRYPRICE STOP LOSS AT $STOP

For example:

    AAPL - $250 CALLS EXPIRATION 10/10 $1.29 STOP LOSS AT $1.00

It will extract the symbol (AAPL), strike price (250), option type (Call),
expiration date (10/10 of this year or next), entry price (1.29) and stop‑loss
price (1.00).  Upon parsing a valid alert it will attempt to submit a bracket
order via the TradeStation REST API using your provided credentials.
"""

import asyncio
import datetime as _dt
import logging
import os
import re
import time
from typing import Optional, Tuple

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    # .env support is optional; if python-dotenv is not installed, nothing happens
    load_dotenv = lambda *args, **kwargs: None  # type: ignore

import discord

# Load environment variables from a .env file if present.  This allows users
# to define their secrets in a local file without committing them to source
# control.  If python-dotenv is not installed this call is a no‑op.
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s:%(name)s: %(message)s",
)
logger = logging.getLogger("bot")


def parse_expiration_date(date_str: str) -> str:
    """Parse an expiration date in `MM/DD` or `MM/DD/YY` or `MM/DD/YYYY` format.

    If the year is omitted, the function assumes the date is in the future
    relative to today; if the date has already passed this year it will roll
    forward to the next year.  The returned string is in ISO format
    (YYYY‑MM‑DD).

    Parameters
    ----------
    date_str: str
        A date string such as ``10/10`` or ``10/10/25``.

    Returns
    -------
    str
        The normalized date in YYYY‑MM‑DD format.
    """
    parts = date_str.split("/")
    today = _dt.date.today()
    if len(parts) == 2:
        month, day = map(int, parts)
        year = today.year
        try:
            dt = _dt.date(year, month, day)
        except ValueError as e:
            raise ValueError(f"Invalid expiration date '{date_str}': {e}")
        # If the date is earlier than or equal to today, roll to next year
        if dt <= today:
            dt = _dt.date(year + 1, month, day)
    elif len(parts) == 3:
        month, day, year = map(int, parts)
        # Support two‑digit years (e.g. 25 for 2025)
        if year < 100:
            year += 2000
        try:
            dt = _dt.date(year, month, day)
        except ValueError as e:
            raise ValueError(f"Invalid expiration date '{date_str}': {e}")
    else:
        raise ValueError(f"Invalid expiration date format: '{date_str}'")
    return dt.isoformat()


def parse_alert_message(content: str) -> Optional[Tuple[str, float, str, str, float, float]]:
    """Parse an alert message and extract trade parameters.

    The expected alert format is described in the module docstring.  If the
    message does not match the expected pattern, the function returns
    ``None``.

    Parameters
    ----------
    content: str
        The raw text of the Discord message.

    Returns
    -------
    Optional[Tuple[str, float, str, str, float, float]]
        A tuple containing (symbol, strike, option_type, expiration_date,
        entry_price, stop_price) if parsing succeeds, otherwise ``None``.
    """
    # Remove custom emoji (e.g. <a:RedAlert:759583962237763595>) and mentions
    cleaned = re.sub(r'<[^>]+>', '', content)
    # Normalize whitespace
    cleaned = " ".join(cleaned.split())
    # Regex pattern with named groups
    pattern = (
        r"(?P<symbol>[A-Za-z]+)\s*-\s*\$(?P<strike>[0-9]+(?:\.[0-9]+)?)\s*"
        r"(?P<otype>CALLS|PUTS)\s*"
        r"EXPIRATION\s*(?P<expiry>[0-9/]+)\s*"
        r"\$(?P<entry>[0-9]+(?:\.[0-9]+)?)\s*"
        r"STOP\s*LOSS\s*AT\s*\$(?P<stop>[0-9]+(?:\.[0-9]+)?)"
    )
    match = re.search(pattern, cleaned, re.IGNORECASE)
    if not match:
        return None
    groups = match.groupdict()
    symbol = groups["symbol"].upper()
    strike = float(groups["strike"])
    otype = groups["otype"].upper()
    option_type = "Call" if otype.startswith("CALL") else "Put"
    expiry_str = groups["expiry"]
    expiration_date = parse_expiration_date(expiry_str)
    entry_price = float(groups["entry"])
    stop_price = float(groups["stop"])
    return (symbol, strike, option_type, expiration_date, entry_price, stop_price)


class TradeStationClient:
    """A minimal client for interacting with the TradeStation REST API.

    This client handles refreshing an OAuth2 access token using a refresh token
    and submitting bracket orders.  It is intentionally simple and may need
    enhancement for production use (e.g. better error handling, retries,
    support for different order types, etc.).
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get(
            "TS_BASE_URL", "https://sim-api.tradestation.com/v3"
        ).rstrip("/")
        self.client_id = os.getenv("TS_CLIENT_ID")
        self.client_secret = os.getenv("TS_CLIENT_SECRET")
        self.account_key = os.getenv("TS_ACCOUNT_KEY")
        self.redirect_uri = os.getenv("TS_REDIRECT_URI")
        self.refresh_token = os.getenv("TS_REFRESH_TOKEN")
        # Access token cache
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        if not all(
            [
                self.client_id,
                self.client_secret,
                self.account_key,
                self.redirect_uri,
                self.refresh_token,
            ]
        ):
            logger.warning(
                "One or more TradeStation environment variables are missing; the bot"
                " will not be able to submit orders until they are provided."
            )

    def _refresh_access_token(self) -> None:
        """Refresh the OAuth2 access token using the refresh token.

        This method updates the internal access token and expiry timestamp.
        """
        token_url = f"{self.base_url}/security/authorize"
        # Some TradeStation endpoints expect `grant_type=refresh_token` at
        # `https://api.tradestation.com/v3/authorize/token`.  The exact path may
        # differ depending on API version.  Adjust as needed.
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "redirect_uri": self.redirect_uri,
        }
        logger.info("Refreshing TradeStation access token...")
        resp = requests.post(token_url, data=data)
        try:
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to refresh access token: %s", e)
            logger.debug("Response: %s", resp.text)
            raise
        token_data = resp.json()
        self._access_token = token_data.get("access_token")
        # expires_in is typically seconds until expiry
        expires_in = float(token_data.get("expires_in", 0))
        self._token_expires_at = time.time() + expires_in - 60  # refresh 60s early
        logger.info("Obtained new access token (expires in %s seconds)", expires_in)

    def _get_access_token(self) -> str:
        """Get a valid access token, refreshing it if necessary."""
        if not self._access_token or time.time() >= self._token_expires_at:
            self._refresh_access_token()
        assert self._access_token is not None  # for type checkers
        return self._access_token

    def submit_bracket_order(
        self,
        symbol: str,
        strike: float,
        option_type: str,
        expiration: str,
        entry_price: float,
        stop_price: float,
        quantity: int = 1,
    ) -> dict:
        """Submit a bracket order to TradeStation.

        Parameters
        ----------
        symbol: str
            The underlying stock symbol (e.g. "AAPL").
        strike: float
            The option strike price.
        option_type: str
            Either "Call" or "Put".
        expiration: str
            The expiration date in ISO format (YYYY‑MM‑DD).
        entry_price: float
            The limit price to pay per contract.
        stop_price: float
            The stop‑loss trigger price per contract.
        quantity: int, optional
            Number of contracts to trade (default is 1).

        Returns
        -------
        dict
            The JSON response from the API.  An exception will be raised if the
            request fails.
        """
        token = self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        # Construct an OSI option symbol.  The TradeStation API accepts either
        # OSI symbols or the underlying symbol with additional option fields.
        # OSI format: ROOT (1‑6 chars), YYMMDD (date), C/P (call/put), and
        # strike price with 5 digits for dollars and 3 for cents.  Example:
        # AAPL  251010C00250000 for AAPL 25/10/25 250 call.  We'll build it.
        expiry_date = _dt.datetime.fromisoformat(expiration)
        yy = expiry_date.strftime("%y")
        mmdd = expiry_date.strftime("%m%d")
        type_code = "C" if option_type.lower().startswith("c") else "P"
        strike_int = int(strike)
        # strike price must be formatted as 8 digits: 5 for dollars, 3 for decimal
        strike_formatted = f"{strike:08.3f}".replace(".", "")
        root = symbol.ljust(6)  # pad to 6 characters
        option_symbol = f"{root}{yy}{mmdd}{type_code}{strike_formatted}"
        # Primary (entry) order
        entry_order = {
            "AccountKey": self.account_key,
            "Symbol": option_symbol,
            "Quantity": quantity,
            "OrderAction": "Buy",
            "OrderType": "Limit",
            "LimitPrice": entry_price,
            "TimeInForce": "Day",
            "Route": "AUTO",
        }
        # Secondary stop‑loss order (sell stop)
        stop_order = {
            "AccountKey": self.account_key,
            "Symbol": option_symbol,
            "Quantity": quantity,
            "OrderAction": "Sell",
            "OrderType": "Stop",
            "StopPrice": stop_price,
            "TimeInForce": "Day",
            "Route": "AUTO",
        }
        payload = {"Orders": [entry_order, stop_order]}
        url = f"{self.base_url}/order/groups"
        logger.info(
            "Submitting bracket order: %s %s %s %s @ %s with stop %s",
            symbol,
            strike,
            option_type,
            expiration,
            entry_price,
            stop_price,
        )
        response = requests.post(url, json=payload, headers=headers)
        try:
            response.raise_for_status()
        except Exception as e:
            logger.error("Order submission failed: %s", e)
            logger.debug("Response: %s", response.text)
            raise
        logger.info("Order submitted successfully: %s", response.json())
        return response.json()


class AlertBot(discord.Client):
    """Discord client that listens for trading alerts and places orders."""

    def __init__(self, channel_id: int, ts_client: TradeStationClient, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel_id = channel_id
        self.ts_client = ts_client

    async def on_ready(self) -> None:
        logger.info("Connected to Discord as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        # Ignore messages from bots or from other channels
        if message.author.bot:
            return
        if message.channel.id != self.channel_id:
            return
        content = message.content
        logger.debug("Received message: %s", content)
        parsed = parse_alert_message(content)
        if not parsed:
            return
        symbol, strike, option_type, expiration, entry_price, stop_price = parsed
        logger.info(
            "Parsed alert: %s %s %s exp %s entry %s stop %s",
            symbol,
            strike,
            option_type,
            expiration,
            entry_price,
            stop_price,
        )
        # Place the order asynchronously in a thread executor to avoid blocking
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                self.ts_client.submit_bracket_order,
                symbol,
                strike,
                option_type,
                expiration,
                entry_price,
                stop_price,
            )
        except Exception as e:
            logger.error("Failed to submit bracket order: %s", e)


def main() -> None:
    # Read configuration from environment
    discord_token = os.getenv("DISCORD_TOKEN")
    channel_id = os.getenv("DISCORD_CHANNEL_ID")
    if not discord_token or not channel_id:
        raise RuntimeError(
            "DISCORD_TOKEN and DISCORD_CHANNEL_ID environment variables must be set"
        )
    try:
        channel_id_int = int(channel_id)
    except ValueError:
        raise RuntimeError("DISCORD_CHANNEL_ID must be an integer")
    ts_client = TradeStationClient()
    # Request privileged intents so the bot can see message content.  The
    # `message_content` intent must be enabled in your Discord bot settings.
    intents = discord.Intents.default()
    intents.message_content = True
    bot = AlertBot(channel_id_int, ts_client, intents=intents)
    try:
        bot.run(discord_token)
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user.")


if __name__ == "__main__":
    main()
